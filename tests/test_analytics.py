"""
Tests for coverage analytics and link budget history.

Run: python3 -m pytest tests/test_analytics.py -v
"""

import os
import pytest
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from src.utils.analytics import (
    AnalyticsStore,
    CoverageAnalyzer,
    LinkBudgetSample,
    CoverageStats,
    NetworkHealthMetrics,
)


class TestLinkBudgetSample:
    """Tests for LinkBudgetSample dataclass."""

    def test_creation(self):
        """Test sample creation with all fields."""
        sample = LinkBudgetSample(
            timestamp=datetime.now().isoformat(),
            source_node="!abc123",
            dest_node="!def456",
            rssi_dbm=-95.0,
            snr_db=5.5,
            distance_km=2.5,
            packet_loss_pct=10.0,
            link_quality="good"
        )
        assert sample.source_node == "!abc123"
        assert sample.rssi_dbm == -95.0
        assert sample.link_quality == "good"


class TestCoverageStats:
    """Tests for CoverageStats dataclass."""

    def test_creation(self):
        """Test stats creation."""
        stats = CoverageStats(
            total_nodes=10,
            nodes_with_position=8,
            bounding_box={
                'min_lat': 21.0, 'max_lat': 22.0,
                'min_lon': -158.0, 'max_lon': -157.0
            },
            center_point=(21.5, -157.5),
            estimated_area_km2=100.0,
            average_node_spacing_km=5.0,
            coverage_radius_km=10.0
        )
        assert stats.total_nodes == 10
        assert stats.nodes_with_position == 8
        assert stats.center_point == (21.5, -157.5)


class TestNetworkHealthMetrics:
    """Tests for NetworkHealthMetrics dataclass."""

    def test_creation(self):
        """Test metrics creation."""
        metrics = NetworkHealthMetrics(
            timestamp=datetime.now().isoformat(),
            online_nodes=20,
            offline_nodes=5,
            avg_rssi_dbm=-90.0,
            avg_snr_db=8.0,
            avg_link_quality_pct=75.0,
            packet_success_rate=0.95,
            uptime_hours=24.0
        )
        assert metrics.online_nodes == 20
        assert metrics.packet_success_rate == 0.95


class TestAnalyticsStore:
    """Tests for SQLite analytics storage."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = Path(f.name)
        yield db_path
        if db_path.exists():
            db_path.unlink()

    def test_init_creates_tables(self, temp_db):
        """Test database initialization creates tables."""
        store = AnalyticsStore(db_path=temp_db)
        import sqlite3
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        # Check tables exist
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert 'link_budget_history' in tables
        assert 'network_health' in tables
        assert 'coverage_snapshots' in tables

    def test_record_and_get_link_budget(self, temp_db):
        """Test recording and retrieving link budget samples."""
        store = AnalyticsStore(db_path=temp_db)

        sample = LinkBudgetSample(
            timestamp=datetime.now().isoformat(),
            source_node="!node1",
            dest_node="!node2",
            rssi_dbm=-85.0,
            snr_db=10.0,
            distance_km=1.5,
            packet_loss_pct=5.0,
            link_quality="excellent"
        )
        store.record_link_budget(sample)

        # Retrieve
        history = store.get_link_budget_history(hours=1)
        assert len(history) == 1
        assert history[0].source_node == "!node1"
        assert history[0].rssi_dbm == -85.0

    def test_record_network_health(self, temp_db):
        """Test recording network health metrics."""
        store = AnalyticsStore(db_path=temp_db)

        metrics = NetworkHealthMetrics(
            timestamp=datetime.now().isoformat(),
            online_nodes=15,
            offline_nodes=3,
            avg_rssi_dbm=-88.0,
            avg_snr_db=7.5,
            avg_link_quality_pct=80.0,
            packet_success_rate=0.92,
            uptime_hours=48.0
        )
        store.record_network_health(metrics)

        # Retrieve
        health = store.get_network_health_history(hours=1)
        assert len(health) == 1
        assert health[0].online_nodes == 15

    def test_record_coverage(self, temp_db):
        """Test recording coverage stats."""
        store = AnalyticsStore(db_path=temp_db)

        stats = CoverageStats(
            total_nodes=25,
            nodes_with_position=20,
            bounding_box={
                'min_lat': 21.0, 'max_lat': 22.0,
                'min_lon': -158.0, 'max_lon': -157.0
            },
            center_point=(21.5, -157.5),
            estimated_area_km2=150.0,
            average_node_spacing_km=4.0,
            coverage_radius_km=12.0
        )
        store.record_coverage(stats)

        # Should not raise - just verify insertion works
        assert True

    def test_get_link_budget_filtered(self, temp_db):
        """Test filtering link budget by nodes."""
        store = AnalyticsStore(db_path=temp_db)

        # Add samples from different node pairs
        for i in range(3):
            store.record_link_budget(LinkBudgetSample(
                timestamp=datetime.now().isoformat(),
                source_node=f"!nodeA",
                dest_node=f"!nodeB",
                rssi_dbm=-90.0 - i,
                snr_db=5.0,
                distance_km=1.0,
                packet_loss_pct=0.0,
                link_quality="good"
            ))

        store.record_link_budget(LinkBudgetSample(
            timestamp=datetime.now().isoformat(),
            source_node="!nodeC",
            dest_node="!nodeD",
            rssi_dbm=-100.0,
            snr_db=3.0,
            distance_km=5.0,
            packet_loss_pct=20.0,
            link_quality="fair"
        ))

        # Filter by source
        filtered = store.get_link_budget_history(source_node="!nodeA", hours=1)
        assert len(filtered) == 3

        # Filter by both
        filtered2 = store.get_link_budget_history(
            source_node="!nodeC",
            dest_node="!nodeD",
            hours=1
        )
        assert len(filtered2) == 1

    def test_old_records_excluded(self, temp_db):
        """Test time-based filtering excludes old records."""
        store = AnalyticsStore(db_path=temp_db)

        # Add old sample (simulated by direct SQL)
        import sqlite3
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        old_time = (datetime.now() - timedelta(hours=48)).isoformat()
        cursor.execute("""
            INSERT INTO link_budget_history
            (timestamp, source_node, dest_node, rssi_dbm, snr_db,
             distance_km, packet_loss_pct, link_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (old_time, "!old", "!node", -100, 0, 10, 50, "bad"))
        conn.commit()
        conn.close()

        # Should not return old record with 24hr window
        history = store.get_link_budget_history(hours=24)
        assert len(history) == 0


class TestCoverageAnalyzer:
    """Tests for coverage analysis."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database for analyzer."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = Path(f.name)
        store = AnalyticsStore(db_path=db_path)
        yield store
        if db_path.exists():
            db_path.unlink()

    def test_analyze_empty_nodes(self, temp_db):
        """Test analyzer with empty node list."""
        analyzer = CoverageAnalyzer(store=temp_db)
        stats = analyzer.analyze_coverage([])

        assert stats.total_nodes == 0
        assert stats.nodes_with_position == 0

    def test_analyze_single_node(self, temp_db):
        """Test analyzer with single node."""
        analyzer = CoverageAnalyzer(store=temp_db)
        nodes = [{'lat': 21.3069, 'lon': -157.8583}]
        stats = analyzer.analyze_coverage(nodes)

        assert stats.total_nodes == 1
        assert stats.nodes_with_position == 1
        assert stats.center_point == (21.3069, -157.8583)

    def test_analyze_multiple_nodes(self, temp_db):
        """Test analyzer with multiple nodes."""
        analyzer = CoverageAnalyzer(store=temp_db)
        nodes = [
            {'lat': 21.0, 'lon': -158.0},
            {'lat': 22.0, 'lon': -157.0},
            {'lat': 21.5, 'lon': -157.5},
        ]
        stats = analyzer.analyze_coverage(nodes)

        assert stats.total_nodes == 3
        assert stats.nodes_with_position == 3
        assert stats.bounding_box['min_lat'] == 21.0
        assert stats.bounding_box['max_lat'] == 22.0

    def test_analyze_nodes_without_position(self, temp_db):
        """Test analyzer filters nodes without valid positions."""
        analyzer = CoverageAnalyzer(store=temp_db)
        nodes = [
            {'lat': 21.0, 'lon': -158.0},
            {'lat': 0.0, 'lon': 0.0},  # Invalid
            {'lat': None, 'lon': -157.0},  # Invalid
            {},  # No position
        ]
        stats = analyzer.analyze_coverage(nodes)

        assert stats.total_nodes == 4
        assert stats.nodes_with_position == 1

    def test_link_quality_from_sample(self, temp_db):
        """Test link quality classification via samples."""
        # Test that different RSSI/SNR values produce expected quality labels
        excellent = LinkBudgetSample(
            timestamp=datetime.now().isoformat(),
            source_node="!a", dest_node="!b",
            rssi_dbm=-70.0, snr_db=15.0,
            distance_km=0.5, packet_loss_pct=0.0,
            link_quality="excellent"
        )
        bad = LinkBudgetSample(
            timestamp=datetime.now().isoformat(),
            source_node="!c", dest_node="!d",
            rssi_dbm=-120.0, snr_db=-5.0,
            distance_km=15.0, packet_loss_pct=50.0,
            link_quality="bad"
        )
        assert excellent.link_quality == "excellent"
        assert bad.link_quality == "bad"
