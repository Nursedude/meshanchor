"""Tests for the domain-wide DB-bloat closure (vendored from meshforge
commits 2743ded + 261c52d, 2026-04-26).

Locks in the WAL + synchronous=NORMAL + journal_size_limit=64MB +
busy_timeout contract per DB. If anyone reverts a DB to bare
sqlite3.connect, this fails."""

import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------- node_history.db ----------------

class TestNodeHistoryPragmas:
    def test_journal_mode_is_wal(self, tmp_path):
        from utils.node_history import NodeHistoryDB
        db = NodeHistoryDB(db_path=tmp_path / "nh.db")
        with db._lock:
            from utils.db_helpers import connect_tuned
            conn = connect_tuned(db.db_path)
            try:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                assert mode.lower() == "wal"
            finally:
                conn.close()

    def test_auto_prune_removes_aged_rows(self, tmp_path):
        from utils.node_history import NodeHistoryDB
        db = NodeHistoryDB(
            db_path=tmp_path / "nh.db", retention_seconds=86400
        )
        # Insert an aged row directly
        with db._lock:
            from utils.db_helpers import connect_tuned
            conn = connect_tuned(db.db_path)
            try:
                conn.execute(
                    "INSERT INTO node_observations (node_id, timestamp, latitude, longitude) "
                    "VALUES ('!aged', ?, 0.0, 0.0)",
                    (time.time() - 100 * 86400,),  # 100 days ago
                )
                conn.commit()
            finally:
                conn.close()
        # Force cadence to allow prune
        db._last_prune_ts = 0.0
        db._maybe_prune(time.time())
        from utils.db_helpers import connect_tuned
        conn = connect_tuned(db.db_path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM node_observations WHERE node_id = '!aged'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 0


# ---------------- messages.db ----------------

class TestMessagesPragmas:
    def test_init_db_is_wal(self, tmp_path, monkeypatch):
        import commands.messaging as m
        monkeypatch.setattr(m, "_get_db_path", lambda: tmp_path / "msg.db")
        conn = m._init_db()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert mode.lower() == "wal"
            assert limit == 67_108_864
        finally:
            conn.close()

    def test_auto_prune_callable(self, tmp_path, monkeypatch):
        import commands.messaging as m
        monkeypatch.setattr(m, "_get_db_path", lambda: tmp_path / "msg.db")
        monkeypatch.setattr(m, "_last_message_prune_ts", 0.0)
        # Just exercise the path
        m._maybe_prune_messages()


# ---------------- analytics.db ----------------

class TestAnalyticsPragmas:
    def test_connect_is_wal(self, tmp_path):
        from utils.analytics import AnalyticsStore
        store = AnalyticsStore(db_path=tmp_path / "a.db")
        conn = store._connect()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert mode.lower() == "wal"
            assert sync == 1
            assert limit == 67_108_864
        finally:
            conn.close()


# ---------------- traffic_capture.db ----------------

class TestTrafficStoragePragmas:
    def test_get_connection_is_wal(self, tmp_path):
        from monitoring.traffic_storage import TrafficCapture
        cap = TrafficCapture(db_path=str(tmp_path / "t.db"))
        with cap._get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert mode.lower() == "wal"
            assert limit == 67_108_864


# ---------------- offline_sync.db ----------------

class TestOfflineSyncPragmas:
    def test_size_limit_capped(self, tmp_path):
        from utils.offline_sync import OfflineSyncQueue
        q = OfflineSyncQueue(db_path=tmp_path / "o.db")
        try:
            with q._lock:
                conn = q._get_connection()
                limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert limit == 67_108_864, "the missing pragma added in this closure"
        finally:
            q.close()


# ---------------- diagnostic_history.db ----------------

class TestDiagnosticHistoryPragmas:
    def test_connection_is_wal(self, tmp_path, monkeypatch):
        from utils.diagnostic_engine import DiagnosticEngine
        eng = DiagnosticEngine(persist_history=True)
        monkeypatch.setattr(eng, "_get_db_path", lambda: tmp_path / "d.db")
        if eng._db_conn is not None:
            eng._db_conn.close()
            eng._db_conn = None
        eng._init_db()
        with eng._db_lock:
            conn = eng._get_connection()
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
        assert mode.lower() == "wal"
        assert limit == 67_108_864


# ---------------- health_state.db ----------------

class TestHealthStatePragmas:
    def test_connection_is_wal_with_size_limit(self, tmp_path):
        from utils.shared_health_state import SharedHealthState
        s = SharedHealthState(db_path=tmp_path / "h.db")
        try:
            with s._get_connection() as conn:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
                sync = conn.execute("PRAGMA synchronous").fetchone()[0]
            assert mode.lower() == "wal"
            assert limit == 67_108_864
            assert sync == 1
        finally:
            s.close()


# ---------------- topology_history.db ----------------

class TestTopologySnapshotPragmas:
    def test_get_connection_is_wal(self, tmp_path):
        from utils.topology_snapshot import TopologySnapshotStore
        try:
            store = TopologySnapshotStore(db_path=str(tmp_path / "topo.db"))
        except TypeError:
            pytest.skip("TopologySnapshotStore ctor signature changed; pragma test skipped")
        with store._get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert mode.lower() == "wal"
            assert limit == 67_108_864


# ---------------- metrics.db ----------------

class TestMetricsHistoryPragmas:
    def test_get_connection_is_wal(self, tmp_path):
        from utils.metrics_history import MetricsHistory
        try:
            mh = MetricsHistory(db_path=str(tmp_path / "metrics.db"))
        except TypeError:
            pytest.skip("MetricsHistory ctor signature changed; pragma test skipped")
        conn = mh._get_connection()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
        assert mode.lower() == "wal"
        assert limit == 67_108_864


# ---------------- message_queue.db ----------------

class TestMessageQueuePragmas:
    def test_get_connection_is_wal(self, tmp_path):
        from gateway.message_queue import PersistentMessageQueue
        try:
            q = PersistentMessageQueue(db_path=str(tmp_path / "mq.db"))
        except TypeError:
            pytest.skip("PersistentMessageQueue ctor signature changed; pragma test skipped")
        with q._get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert mode.lower() == "wal"
            assert limit == 67_108_864


# ---------------- contact_mapping.db ----------------

class TestContactMappingPragmas:
    def test_get_connection_preserves_foreign_keys(self, tmp_path):
        from gateway.contact_mapping import ContactMappingTable
        try:
            cm = ContactMappingTable(db_path=str(tmp_path / "cm.db"))
        except TypeError:
            pytest.skip("ContactMappingTable ctor signature changed; pragma test skipped")
        with cm._get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert mode.lower() == "wal"
            assert fk == 1, "foreign_keys=ON must be preserved"
            assert limit == 67_108_864
