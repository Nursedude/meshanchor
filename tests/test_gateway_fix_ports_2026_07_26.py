"""Pins for the 2026-07-26 review fixes ported from MeshForge (lead repo).

C4  node heard-time: MeshCore ISO-8601 strings parse (#51 shape); genuine
    garbage and absent/0 lastHeard land on the can't-attribute leg, never
    "online / heard now".
C5  message_queue.cleanup_stale: the DROPPED witness derives from what the
    UPDATE actually changed — a row concurrently mark_delivered'ed between
    observation and update is not witnessed as dropped.

The claw/broadcast-bridge classes from MeshForge's
tests/test_gateway_claw_fixes_2026_07_26.py are NOT ported — MeshAnchor has
no claw_battery or meshtastic_broadcast_bridge.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

import gateway.message_queue as mq_mod
from gateway.message_queue import PersistentMessageQueue
from gateway.node_models import UnifiedNode


# ---------------------------------------------------------------------------
# C4 — heard-time honesty (ISO strings parse; garbage/absent is not heard-now)
# ---------------------------------------------------------------------------


class TestResolveHeardIsoStrings:
    def _mc(self, **extra):
        base = {"adv_name": "MC", "pubkey_prefix": "aa11bb22cc33"}
        base.update(extra)
        return UnifiedNode.from_meshcore(base)

    def test_stale_iso_string_reads_offline(self):
        stale_iso = (datetime.now() - timedelta(hours=2)).isoformat()
        node = self._mc(last_seen=stale_iso)
        assert node.is_online is False
        assert node.last_seen is not None
        assert (datetime.now() - node.last_seen) > timedelta(hours=1)

    def test_recent_iso_string_reads_online(self):
        recent_iso = (datetime.now() - timedelta(minutes=2)).isoformat()
        node = self._mc(last_seen=recent_iso)
        assert node.is_online is True

    def test_garbage_heard_time_is_not_heard_now(self):
        node = self._mc(last_seen="certainly-not-a-date")
        assert node.is_online is False
        assert node.last_seen is None

    def test_live_advertisement_still_heard_now(self):
        node = self._mc()
        assert node.is_online is True


class TestFromMeshtasticAbsentLastHeard:
    def _node(self, **extra):
        base = {"num": 0x12345678,
                "user": {"longName": "X", "shortName": "X"}}
        base.update(extra)
        return UnifiedNode.from_meshtastic(base)

    def test_absent_lastheard_is_not_online(self):
        node = self._node()
        assert node.is_online is False
        assert node.last_seen is None

    def test_zero_lastheard_is_not_online(self):
        node = self._node(lastHeard=0)
        assert node.is_online is False
        assert node.last_seen is None


# ---------------------------------------------------------------------------
# C5 — cleanup_stale witness derives from what the UPDATE changed
# ---------------------------------------------------------------------------


class TestCleanupStaleWitnessDerivesFromUpdate:
    @staticmethod
    def _queue(tmp_path):
        q = PersistentMessageQueue(db_path=str(tmp_path / "q.db"))
        # MeshAnchor's enqueue refuses (returns None) when no sender is
        # registered for the destination (its Issue #67 divergence); register
        # a no-op one. hasattr-guarded so this file runs on the MeshForge twin.
        if hasattr(q, "register_sender"):
            q.register_sender("meshtastic", lambda payload: True)
        return q

    @staticmethod
    def _make_stale(q, msg_id):
        q.mark_in_progress(msg_id)
        old = (datetime.now() - timedelta(seconds=600)).isoformat()
        with q._get_connection() as conn:
            conn.execute("UPDATE messages SET updated_at = ? WHERE id = ?",
                         (old, msg_id))

    @staticmethod
    def _status(q, msg_id):
        with q._get_connection() as conn:
            return conn.execute(
                "SELECT status FROM messages WHERE id = ?",
                (msg_id,)).fetchone()["status"]

    def test_concurrently_delivered_row_not_witnessed_dropped(
            self, tmp_path, monkeypatch):
        q = self._queue(tmp_path)
        mid = q.enqueue({"text": "x"}, "meshtastic", max_retries=1)
        assert mid is not None
        self._make_stale(q, mid)
        # Force the per-row fallback so the observe→update window exists,
        # then simulate a concurrent mark_delivered inside it.
        monkeypatch.setattr(mq_mod, "_SQLITE_SUPPORTS_RETURNING", False)
        orig = PersistentMessageQueue._stale_exhausted_candidates

        def race(self_q, conn, cutoff):
            rows = orig(self_q, conn, cutoff)
            conn.execute("UPDATE messages SET status = 'delivered' "
                         "WHERE id = ?", (mid,))
            return rows

        monkeypatch.setattr(PersistentMessageQueue,
                            "_stale_exhausted_candidates", race)
        drops = []
        monkeypatch.setattr(mq_mod._dc, "record",
                            lambda *a, **k: drops.append((a, k)))
        assert q.cleanup_stale() == 0
        assert self._status(q, mid) == "delivered"
        assert drops == [], "a delivered row must not be witnessed as DROPPED"
        assert q.get_stats()["failed"] == 0

    def test_fallback_path_still_dead_letters_and_witnesses(
            self, tmp_path, monkeypatch):
        q = self._queue(tmp_path)
        mid = q.enqueue({"text": "x"}, "meshtastic", max_retries=1)
        assert mid is not None
        self._make_stale(q, mid)
        monkeypatch.setattr(mq_mod, "_SQLITE_SUPPORTS_RETURNING", False)
        assert q.cleanup_stale() == 1
        assert self._status(q, mid) == "dead_letter"
        assert q.get_stats()["failed"] == 1

    @pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 35, 0),
                        reason="RETURNING needs SQLite >= 3.35")
    def test_returning_path_dead_letters_and_witnesses(self, tmp_path):
        q = self._queue(tmp_path)
        mid = q.enqueue({"text": "x"}, "meshtastic", max_retries=1)
        assert mid is not None
        self._make_stale(q, mid)
        assert mq_mod._SQLITE_SUPPORTS_RETURNING is True
        assert q.cleanup_stale() == 1
        assert self._status(q, mid) == "dead_letter"
        assert q.get_stats()["failed"] == 1
