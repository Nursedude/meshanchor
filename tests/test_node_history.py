"""Tests for NodeHistoryDB - SQLite node position and state tracking."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import time
from pathlib import Path
from unittest.mock import patch

import pytest


class TestNodeHistoryDB:
    """Test the node history database."""

    def _make_db(self, tmp_path):
        """Create a history DB in a temp directory."""
        from utils.node_history import NodeHistoryDB
        return NodeHistoryDB(db_path=tmp_path / "test_history.db")

    def _make_feature(self, node_id="!abc123", name="Test Node",
                      lat=21.3069, lon=-157.8583, is_online=True,
                      snr=8.5, battery=85, network="meshtastic",
                      hardware="HELTEC_V3", role="ROUTER",
                      via_mqtt=False):
        """Create a minimal GeoJSON feature for testing."""
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat],
            },
            "properties": {
                "id": node_id,
                "name": name,
                "is_online": is_online,
                "snr": snr,
                "battery": battery,
                "network": network,
                "hardware": hardware,
                "role": role,
                "via_mqtt": via_mqtt,
            }
        }

    def test_init_creates_db(self, tmp_path):
        """DB file is created on init."""
        db = self._make_db(tmp_path)
        assert db.db_path.exists()

    def test_record_single_observation(self, tmp_path):
        """Records a single node observation."""
        db = self._make_db(tmp_path)
        features = [self._make_feature()]

        count = db.record_observations(features)
        assert count == 1

    def test_record_multiple_observations(self, tmp_path):
        """Records multiple node observations in one batch."""
        db = self._make_db(tmp_path)
        features = [
            self._make_feature("!node1", lat=21.3, lon=-157.8),
            self._make_feature("!node2", lat=20.8, lon=-156.3),
            self._make_feature("!node3", lat=19.7, lon=-155.1),
        ]

        count = db.record_observations(features)
        assert count == 3

    def test_record_skips_no_coordinates(self, tmp_path):
        """Skips features without valid coordinates."""
        db = self._make_db(tmp_path)
        bad_feature = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": []},
            "properties": {"id": "!bad"}
        }

        count = db.record_observations([bad_feature])
        assert count == 0

    def test_record_skips_no_id(self, tmp_path):
        """Skips features without node ID."""
        db = self._make_db(tmp_path)
        no_id = self._make_feature()
        no_id["properties"]["id"] = ""

        count = db.record_observations([no_id])
        assert count == 0

    def test_throttle_prevents_flooding(self, tmp_path):
        """Same node can't be recorded within MIN_RECORD_INTERVAL."""
        db = self._make_db(tmp_path)
        features = [self._make_feature("!same")]

        count1 = db.record_observations(features)
        count2 = db.record_observations(features)

        assert count1 == 1
        assert count2 == 0  # Throttled

    def test_throttle_different_nodes_allowed(self, tmp_path):
        """Different nodes can be recorded at same time."""
        db = self._make_db(tmp_path)

        count1 = db.record_observations([self._make_feature("!node1")])
        count2 = db.record_observations([self._make_feature("!node2")])

        assert count1 == 1
        assert count2 == 1

    def test_get_trajectory_empty(self, tmp_path):
        """Trajectory for unknown node returns empty list."""
        db = self._make_db(tmp_path)
        trajectory = db.get_trajectory("!unknown")
        assert trajectory == []

    def test_get_trajectory_single_point(self, tmp_path):
        """Trajectory with one observation."""
        db = self._make_db(tmp_path)
        db.record_observations([self._make_feature("!node1")])

        trajectory = db.get_trajectory("!node1", hours=1)
        assert len(trajectory) == 1
        assert trajectory[0].node_id == "!node1"
        assert trajectory[0].latitude == 21.3069
        assert trajectory[0].longitude == -157.8583

    def test_get_trajectory_ordered_by_time(self, tmp_path):
        """Trajectory returns observations in time order."""
        from utils.node_history import NodeHistoryDB, MIN_RECORD_INTERVAL
        db = self._make_db(tmp_path)

        # Insert directly to bypass throttle
        import sqlite3
        conn = sqlite3.connect(str(db.db_path))
        now = time.time()
        conn.execute("""
            INSERT INTO node_observations
            (node_id, timestamp, latitude, longitude, is_online, network, name)
            VALUES (?, ?, ?, ?, 1, 'meshtastic', 'Node')
        """, ("!node1", now - 300, 21.0, -157.0))
        conn.execute("""
            INSERT INTO node_observations
            (node_id, timestamp, latitude, longitude, is_online, network, name)
            VALUES (?, ?, ?, ?, 1, 'meshtastic', 'Node')
        """, ("!node1", now - 100, 21.1, -157.1))
        conn.execute("""
            INSERT INTO node_observations
            (node_id, timestamp, latitude, longitude, is_online, network, name)
            VALUES (?, ?, ?, ?, 1, 'meshtastic', 'Node')
        """, ("!node1", now - 200, 21.05, -157.05))
        conn.commit()
        conn.close()

        trajectory = db.get_trajectory("!node1", hours=1)
        assert len(trajectory) == 3
        # Should be ordered oldest first
        assert trajectory[0].latitude == 21.0
        assert trajectory[1].latitude == 21.05
        assert trajectory[2].latitude == 21.1

    def test_get_trajectory_respects_hours(self, tmp_path):
        """Trajectory only returns observations within time window."""
        db = self._make_db(tmp_path)

        import sqlite3
        conn = sqlite3.connect(str(db.db_path))
        now = time.time()
        # One recent, one old
        conn.execute("""
            INSERT INTO node_observations
            (node_id, timestamp, latitude, longitude, is_online, network, name)
            VALUES (?, ?, ?, ?, 1, 'meshtastic', 'Node')
        """, ("!node1", now - 60, 21.0, -157.0))
        conn.execute("""
            INSERT INTO node_observations
            (node_id, timestamp, latitude, longitude, is_online, network, name)
            VALUES (?, ?, ?, ?, 1, 'meshtastic', 'Node')
        """, ("!node1", now - 7200, 20.0, -156.0))  # 2 hours ago
        conn.commit()
        conn.close()

        trajectory = db.get_trajectory("!node1", hours=1)
        assert len(trajectory) == 1
        assert trajectory[0].latitude == 21.0

    def test_get_snapshot_latest_per_node(self, tmp_path):
        """Snapshot returns most recent observation per node."""
        db = self._make_db(tmp_path)

        import sqlite3
        conn = sqlite3.connect(str(db.db_path))
        now = time.time()
        # Two observations for same node - should get latest
        conn.execute("""
            INSERT INTO node_observations
            (node_id, timestamp, latitude, longitude, is_online, network, name)
            VALUES (?, ?, ?, ?, 1, 'meshtastic', 'Node 1')
        """, ("!node1", now - 60, 21.0, -157.0))
        conn.execute("""
            INSERT INTO node_observations
            (node_id, timestamp, latitude, longitude, is_online, network, name)
            VALUES (?, ?, ?, ?, 1, 'meshtastic', 'Node 1 Updated')
        """, ("!node1", now - 10, 21.1, -157.1))
        # One observation for another node
        conn.execute("""
            INSERT INTO node_observations
            (node_id, timestamp, latitude, longitude, is_online, network, name)
            VALUES (?, ?, ?, ?, 1, 'meshtastic', 'Node 2')
        """, ("!node2", now - 30, 20.5, -156.5))
        conn.commit()
        conn.close()

        snapshot = db.get_snapshot(timestamp=now)
        assert len(snapshot) == 2

        node1 = next(o for o in snapshot if o.node_id == "!node1")
        assert node1.latitude == 21.1  # Latest observation
        assert node1.name == "Node 1 Updated"

    def test_get_snapshot_respects_window(self, tmp_path):
        """Snapshot only includes nodes within window."""
        db = self._make_db(tmp_path)

        import sqlite3
        conn = sqlite3.connect(str(db.db_path))
        now = time.time()
        # One within window, one outside
        conn.execute("""
            INSERT INTO node_observations
            (node_id, timestamp, latitude, longitude, is_online, network, name)
            VALUES (?, ?, ?, ?, 1, 'meshtastic', 'Recent')
        """, ("!recent", now - 60, 21.0, -157.0))
        conn.execute("""
            INSERT INTO node_observations
            (node_id, timestamp, latitude, longitude, is_online, network, name)
            VALUES (?, ?, ?, ?, 1, 'meshtastic', 'Old')
        """, ("!old", now - 600, 20.0, -156.0))
        conn.commit()
        conn.close()

        snapshot = db.get_snapshot(timestamp=now, window_seconds=300)
        assert len(snapshot) == 1
        assert snapshot[0].node_id == "!recent"

    def test_get_unique_nodes(self, tmp_path):
        """Returns summary of unique nodes with counts."""
        db = self._make_db(tmp_path)

        import sqlite3
        conn = sqlite3.connect(str(db.db_path))
        now = time.time()
        # Node 1: 3 observations
        for i in range(3):
            conn.execute("""
                INSERT INTO node_observations
                (node_id, timestamp, latitude, longitude, is_online, network, name)
                VALUES (?, ?, ?, ?, 1, 'meshtastic', 'Node 1')
            """, ("!node1", now - (i * 120), 21.0, -157.0))
        # Node 2: 1 observation
        conn.execute("""
            INSERT INTO node_observations
            (node_id, timestamp, latitude, longitude, is_online, network, name)
            VALUES (?, ?, ?, ?, 1, 'rns', 'Node 2')
        """, ("!node2", now - 60, 20.5, -156.5))
        conn.commit()
        conn.close()

        nodes = db.get_unique_nodes(hours=1)
        assert len(nodes) == 2

        node1 = next(n for n in nodes if n["node_id"] == "!node1")
        assert node1["observation_count"] == 3
        assert node1["name"] == "Node 1"

    def test_get_trajectory_geojson(self, tmp_path):
        """Trajectory GeoJSON has correct LineString format."""
        db = self._make_db(tmp_path)

        import sqlite3
        conn = sqlite3.connect(str(db.db_path))
        now = time.time()
        points = [(21.0, -157.0), (21.1, -157.1), (21.2, -157.2)]
        for i, (lat, lon) in enumerate(points):
            conn.execute("""
                INSERT INTO node_observations
                (node_id, timestamp, latitude, longitude, is_online, network, name)
                VALUES (?, ?, ?, ?, 1, 'meshtastic', 'Moving Node')
            """, ("!mover", now - (300 - i * 100), lat, lon))
        conn.commit()
        conn.close()

        geojson = db.get_trajectory_geojson("!mover", hours=1)

        assert geojson["type"] == "Feature"
        assert geojson["geometry"]["type"] == "LineString"
        assert len(geojson["geometry"]["coordinates"]) == 3
        # GeoJSON is [lon, lat]
        assert geojson["geometry"]["coordinates"][0] == [-157.0, 21.0]
        assert geojson["properties"]["point_count"] == 3
        assert geojson["properties"]["node_id"] == "!mover"
        assert geojson["properties"]["name"] == "Moving Node"

    def test_get_trajectory_geojson_empty(self, tmp_path):
        """Trajectory GeoJSON for unknown node has null geometry."""
        db = self._make_db(tmp_path)
        geojson = db.get_trajectory_geojson("!nonexistent")

        assert geojson["type"] == "Feature"
        assert geojson["geometry"] is None
        assert geojson["properties"]["node_id"] == "!nonexistent"

    def test_get_stats(self, tmp_path):
        """Stats returns correct database statistics."""
        db = self._make_db(tmp_path)

        # Empty DB
        stats = db.get_stats()
        assert stats["total_observations"] == 0
        assert stats["unique_nodes"] == 0
        assert stats["oldest_record"] is None

        # Add some data
        db.record_observations([
            self._make_feature("!a"),
            self._make_feature("!b"),
        ])

        stats = db.get_stats()
        assert stats["total_observations"] == 2
        assert stats["unique_nodes"] == 2
        assert stats["oldest_record"] is not None
        assert stats["newest_record"] is not None
        assert stats["db_size_kb"] > 0

    def test_cleanup_removes_old_records(self, tmp_path):
        """Cleanup deletes observations older than retention."""
        from utils.node_history import NodeHistoryDB
        db = NodeHistoryDB(db_path=tmp_path / "test.db", retention_seconds=100)

        import sqlite3
        conn = sqlite3.connect(str(db.db_path))
        now = time.time()
        # One old, one recent
        conn.execute("""
            INSERT INTO node_observations
            (node_id, timestamp, latitude, longitude, is_online, network, name)
            VALUES (?, ?, ?, ?, 1, 'meshtastic', 'Old')
        """, ("!old", now - 200, 21.0, -157.0))
        conn.execute("""
            INSERT INTO node_observations
            (node_id, timestamp, latitude, longitude, is_online, network, name)
            VALUES (?, ?, ?, ?, 1, 'meshtastic', 'Recent')
        """, ("!recent", now - 10, 21.0, -157.0))
        conn.commit()
        conn.close()

        deleted = db.cleanup()
        assert deleted == 1

        stats = db.get_stats()
        assert stats["total_observations"] == 1

    def test_cleanup_no_old_records(self, tmp_path):
        """Cleanup with no old records deletes nothing."""
        db = self._make_db(tmp_path)
        db.record_observations([self._make_feature()])

        deleted = db.cleanup()
        assert deleted == 0

    def test_observation_dataclass_fields(self, tmp_path):
        """NodeObservation has all expected fields."""
        db = self._make_db(tmp_path)
        db.record_observations([self._make_feature(
            snr=7.5, battery=92, hardware="RAK4631",
            role="CLIENT", via_mqtt=True
        )])

        trajectory = db.get_trajectory("!abc123", hours=1)
        assert len(trajectory) == 1
        obs = trajectory[0]

        assert obs.node_id == "!abc123"
        assert obs.name == "Test Node"
        assert obs.snr == 7.5
        assert obs.battery == 92
        assert obs.hardware == "RAK4631"
        assert obs.role == "CLIENT"
        assert obs.via_mqtt is True
        assert obs.is_online is True
        assert obs.network == "meshtastic"

    def test_thread_safety(self, tmp_path):
        """Concurrent writes don't corrupt the database."""
        import threading
        db = self._make_db(tmp_path)

        errors = []

        def write_nodes(prefix, count):
            try:
                for i in range(count):
                    features = [self._make_feature(
                        f"!{prefix}_{i}", lat=21.0 + i * 0.01, lon=-157.0
                    )]
                    # Bypass throttle by manipulating internal state
                    db._last_recorded.pop(f"!{prefix}_{i}", None)
                    db.record_observations(features)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=write_nodes, args=("a", 10)),
            threading.Thread(target=write_nodes, args=("b", 10)),
            threading.Thread(target=write_nodes, args=("c", 10)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        stats = db.get_stats()
        assert stats["total_observations"] == 30
        assert stats["unique_nodes"] == 30


class TestNodeHistoryIntegration:
    """Integration tests with MapDataCollector."""

    def test_collector_records_to_history(self, tmp_path):
        """MapDataCollector automatically records to history DB."""
        from utils.map_data_service import MapDataCollector
        from unittest.mock import patch, MagicMock

        collector = MapDataCollector(cache_dir=tmp_path, enable_history=True)

        # Verify history DB was created
        assert collector._history is not None

        # Mock a source to return data
        feature = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-157.8, 21.3]},
            "properties": {
                "id": "!test_hist",
                "name": "History Test",
                "is_online": True,
                "network": "meshtastic",
                "snr": 6.0,
                "battery": 75,
            }
        }

        with patch.object(collector, '_collect_meshtasticd', return_value=[feature]):
            collector.collect(max_age_seconds=0)

        # Check history recorded
        stats = collector._history.get_stats()
        assert stats["total_observations"] == 1
        assert stats["unique_nodes"] == 1

    def test_collector_history_disabled(self, tmp_path):
        """MapDataCollector works without history."""
        from utils.map_data_service import MapDataCollector

        collector = MapDataCollector(cache_dir=tmp_path, enable_history=False)
        assert collector._history is None

        # Should still collect without errors
        result = collector.collect(max_age_seconds=0)
        assert result["type"] == "FeatureCollection"

    def test_collector_history_error_non_fatal(self, tmp_path):
        """History recording errors don't break collection."""
        from utils.map_data_service import MapDataCollector
        from unittest.mock import patch

        collector = MapDataCollector(cache_dir=tmp_path, enable_history=True)

        feature = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-157.8, 21.3]},
            "properties": {"id": "!test", "name": "Test", "is_online": True}
        }

        # Make history recording fail
        with patch.object(collector._history, 'record_observations',
                         side_effect=Exception("DB error")):
            with patch.object(collector, '_collect_meshtasticd', return_value=[feature]):
                result = collector.collect(max_age_seconds=0)

        # Collection still succeeds
        assert len(result["features"]) == 1
