"""Tests for SharedHealthState multi-process coordination."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.shared_health_state import (
    SharedHealthState,
    HealthState,
    ServiceHealthRecord,
    HealthEvent,
    create_shared_state,
    integrate_with_active_probe,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = Path(f.name)
    yield db_path
    # Cleanup
    if db_path.exists():
        db_path.unlink()


class TestSharedHealthState:
    """Test SharedHealthState functionality."""

    def test_initialization(self, temp_db):
        """Test that SharedHealthState initializes correctly."""
        state = SharedHealthState(db_path=temp_db)
        assert state.db_path == temp_db
        assert state.process_id.startswith("pid-")
        state.close()

    def test_update_service_new(self, temp_db):
        """Test updating a new service."""
        state = SharedHealthState(db_path=temp_db)

        changed = state.update_service("meshtasticd", "healthy", "test_success", 42.5)
        assert changed is True  # First update is always a change from unknown

        record = state.get_service("meshtasticd")
        assert record is not None
        assert record.service == "meshtasticd"
        assert record.state == HealthState.HEALTHY
        assert record.reason == "test_success"
        assert record.latency_ms == 42.5
        assert record.uptime_pct == 100.0

        state.close()

    def test_update_service_state_change(self, temp_db):
        """Test that state changes are detected."""
        state = SharedHealthState(db_path=temp_db)

        # Initial healthy
        state.update_service("rnsd", "healthy")

        # Change to unhealthy
        changed = state.update_service("rnsd", "unhealthy", "timeout")
        assert changed is True

        record = state.get_service("rnsd")
        assert record.state == HealthState.UNHEALTHY
        assert record.consecutive_fails == 1

        state.close()

    def test_update_service_no_change(self, temp_db):
        """Test that no-change updates return False."""
        state = SharedHealthState(db_path=temp_db)

        state.update_service("service", "healthy")
        changed = state.update_service("service", "healthy")
        assert changed is False

        state.close()

    def test_consecutive_counters(self, temp_db):
        """Test consecutive pass/fail counters."""
        state = SharedHealthState(db_path=temp_db)

        # Multiple healthy checks
        for _ in range(3):
            state.update_service("test", "healthy")

        record = state.get_service("test")
        assert record.consecutive_passes == 3
        assert record.consecutive_fails == 0

        # Now fail
        state.update_service("test", "unhealthy")
        record = state.get_service("test")
        assert record.consecutive_fails == 1
        assert record.consecutive_passes == 0

        state.close()

    def test_uptime_calculation(self, temp_db):
        """Test uptime percentage calculation."""
        state = SharedHealthState(db_path=temp_db)

        # 3 healthy, 1 unhealthy = 75% uptime
        state.update_service("test", "healthy")
        state.update_service("test", "healthy")
        state.update_service("test", "healthy")
        state.update_service("test", "unhealthy")

        record = state.get_service("test")
        assert record.uptime_pct == 75.0

        state.close()

    def test_get_all_services(self, temp_db):
        """Test getting all tracked services."""
        state = SharedHealthState(db_path=temp_db)

        state.update_service("service1", "healthy")
        state.update_service("service2", "unhealthy")
        state.update_service("service3", "recovering")

        services = state.get_all_services()
        assert len(services) == 3

        names = [s.service for s in services]
        assert "service1" in names
        assert "service2" in names
        assert "service3" in names

        state.close()

    def test_is_healthy(self, temp_db):
        """Test is_healthy convenience method."""
        state = SharedHealthState(db_path=temp_db)

        state.update_service("healthy_service", "healthy")
        state.update_service("unhealthy_service", "unhealthy")

        assert state.is_healthy("healthy_service") is True
        assert state.is_healthy("unhealthy_service") is False
        assert state.is_healthy("nonexistent") is False

        state.close()

    def test_is_stale(self, temp_db):
        """Test stale detection."""
        state = SharedHealthState(db_path=temp_db, stale_threshold=1)

        state.update_service("test", "healthy")
        assert state.is_stale("test") is False

        # Wait for stale threshold
        time.sleep(1.1)
        assert state.is_stale("test") is True

        state.close()

    def test_get_stale_services(self, temp_db):
        """Test getting list of stale services."""
        state = SharedHealthState(db_path=temp_db, stale_threshold=1)

        state.update_service("fresh", "healthy")
        state.update_service("stale", "healthy")

        # Make stale older by waiting
        time.sleep(1.1)
        state.update_service("fresh", "healthy")  # Refresh one

        stale = state.get_stale_services()
        assert "stale" in stale
        assert "fresh" not in stale

        state.close()

    def test_health_events_logged(self, temp_db):
        """Test that state transitions are logged as events."""
        state = SharedHealthState(db_path=temp_db)

        state.update_service("test", "healthy")
        state.update_service("test", "unhealthy", "timeout")

        events = state.get_recent_events(service="test")
        assert len(events) == 2

        # Events are returned newest first (ORDER BY timestamp DESC)
        # Second event (newest): healthy -> unhealthy
        assert events[0].old_state == "healthy"
        assert events[0].new_state == "unhealthy"
        assert events[0].reason == "timeout"

        # First event (oldest): unknown -> healthy
        assert events[1].old_state == "unknown"
        assert events[1].new_state == "healthy"

        state.close()

    def test_get_recent_events_filtering(self, temp_db):
        """Test event filtering by service and time."""
        state = SharedHealthState(db_path=temp_db)

        state.update_service("service1", "healthy")
        state.update_service("service2", "healthy")
        state.update_service("service1", "unhealthy")

        # Filter by service
        events = state.get_recent_events(service="service1")
        assert len(events) == 2
        assert all(e.service == "service1" for e in events)

        # All events
        all_events = state.get_recent_events()
        assert len(all_events) == 3

        state.close()

    def test_get_metrics(self, temp_db):
        """Test aggregated metrics."""
        state = SharedHealthState(db_path=temp_db)

        state.update_service("healthy1", "healthy")
        state.update_service("healthy2", "healthy")
        state.update_service("unhealthy1", "unhealthy")

        metrics = state.get_metrics()
        assert metrics["total_services"] == 3
        assert metrics["healthy_count"] == 2
        assert metrics["unhealthy_count"] == 1
        assert "timestamp" in metrics

        state.close()

    def test_latency_percentiles(self, temp_db):
        """Test latency percentile calculations."""
        state = SharedHealthState(db_path=temp_db)

        # Record multiple latency samples
        for latency in [10.0, 20.0, 30.0, 40.0, 50.0]:
            state.update_service("test", "healthy", latency_ms=latency)

        percentiles = state.get_latency_percentiles("test", hours=1)
        assert percentiles["count"] == 5
        assert percentiles["min"] == 10.0
        assert percentiles["max"] == 50.0
        assert percentiles["avg"] == 30.0

        state.close()

    def test_purge_old_data(self, temp_db):
        """Test purging old historical data."""
        state = SharedHealthState(db_path=temp_db)

        state.update_service("test", "healthy")
        state.update_service("test", "unhealthy")

        # Purge with 0 days should remove everything
        result = state.purge_old_data(days=0)
        assert result["events_deleted"] >= 0
        assert result["samples_deleted"] >= 0

        state.close()

    def test_clear_service(self, temp_db):
        """Test removing a service from tracking."""
        state = SharedHealthState(db_path=temp_db)

        state.update_service("to_remove", "healthy")
        state.update_service("to_keep", "healthy")

        removed = state.clear_service("to_remove")
        assert removed is True

        assert state.get_service("to_remove") is None
        assert state.get_service("to_keep") is not None

        state.close()

    def test_clear_all(self, temp_db):
        """Test clearing all health state."""
        state = SharedHealthState(db_path=temp_db)

        state.update_service("service1", "healthy")
        state.update_service("service2", "healthy")

        cleared = state.clear_all()
        assert cleared == 2

        services = state.get_all_services()
        assert len(services) == 0

        state.close()

    def test_create_shared_state_factory(self, temp_db):
        """Test create_shared_state factory function."""
        with patch('utils.shared_health_state.get_real_user_home') as mock_home:
            mock_home.return_value = temp_db.parent
            state = create_shared_state()
            assert state is not None
            state.close()

    def test_thread_safety(self, temp_db):
        """Test that SharedHealthState is thread-safe."""
        state = SharedHealthState(db_path=temp_db)
        errors = []

        def writer(service_num):
            try:
                for i in range(10):
                    state.update_service(
                        f"service_{service_num}",
                        "healthy" if i % 2 == 0 else "unhealthy"
                    )
            except Exception as e:
                errors.append(e)

        import threading
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        state.close()


class TestServiceHealthRecord:
    """Test ServiceHealthRecord dataclass."""

    def test_to_dict(self):
        """Test converting to dictionary."""
        record = ServiceHealthRecord(
            service="test",
            state=HealthState.HEALTHY,
            reason="success",
            latency_ms=25.0,
            updated_at=time.time(),
            updated_by="test",
            consecutive_passes=3,
            consecutive_fails=0,
            uptime_pct=99.5,
        )

        d = record.to_dict()
        assert d["service"] == "test"
        assert d["state"] == "healthy"
        assert d["reason"] == "success"
        assert d["latency_ms"] == 25.0


class TestHealthEvent:
    """Test HealthEvent dataclass."""

    def test_to_dict(self):
        """Test converting to dictionary."""
        event = HealthEvent(
            id=1,
            service="test",
            old_state="healthy",
            new_state="unhealthy",
            reason="timeout",
            timestamp=time.time(),
            process_id="pid-123",
        )

        d = event.to_dict()
        assert d["service"] == "test"
        assert d["old_state"] == "healthy"
        assert d["new_state"] == "unhealthy"
        assert d["reason"] == "timeout"


class TestIntegration:
    """Test integration with ActiveHealthProbe."""

    def test_integrate_with_active_probe(self, temp_db):
        """Test that integrate_with_active_probe registers callback."""
        state = SharedHealthState(db_path=temp_db)

        # Create a mock probe
        class MockProbe:
            def __init__(self):
                self.callbacks = {}

            def register_callback(self, event, callback):
                self.callbacks[event] = callback

            def get_status(self, service):
                return {"last_result": {"reason": "test", "latency_ms": 10.0}}

        probe = MockProbe()
        integrate_with_active_probe(state, probe)

        assert "on_state_change" in probe.callbacks

        # Simulate a state change
        class MockState:
            value = "healthy"

        probe.callbacks["on_state_change"]("test_service", MockState())

        # Check that state was updated
        record = state.get_service("test_service")
        assert record is not None
        assert record.state == HealthState.HEALTHY

        state.close()
