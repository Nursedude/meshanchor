"""``utils.db_helpers.ensure_column`` — the one additive-column helper.

Ported with MeshForge's tests (test_messaging_db.TestEnsureColumn) alongside
MAP-H H14's ``node_history.radio_heard`` column, 2026-11-09.
"""

import sqlite3

import pytest

from utils.db_helpers import connect_tuned, ensure_column


class _StaleSchemaConn:
    """A connection whose FIRST schema read happens just before another
    process adds the column — PRAGMA-then-ALTER is not atomic."""

    def __init__(self, real, winner_sql_path):
        self.real, self.path, self.n = real, winner_sql_path, 0

    def execute(self, sql, *a):
        if sql.startswith("PRAGMA table_info"):
            self.n += 1
            if self.n == 1:
                w = connect_tuned(self.path)
                w.execute("ALTER TABLE t ADD COLUMN c INTEGER")
                w.commit()
                w.close()
                return [(0, "id", "INTEGER", 0, None, 1)]
        return self.real.execute(sql, *a)


def _db(tmp_path):
    path = tmp_path / "x.db"
    c = connect_tuned(path)
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    c.commit()
    return path, c


def _cols(conn):
    return [r[1] for r in conn.execute("PRAGMA table_info(t)")]


def test_adds_once_then_noop(tmp_path):
    _, c = _db(tmp_path)
    assert ensure_column(c, "t", "c", "INTEGER") is True
    assert ensure_column(c, "t", "c", "INTEGER") is False
    assert _cols(c).count("c") == 1
    c.close()


def test_losing_the_race_is_tolerated(tmp_path):
    path, c = _db(tmp_path)
    stale = _StaleSchemaConn(c, path)
    assert ensure_column(stale, "t", "c", "INTEGER") is False
    assert _cols(c).count("c") == 1
    c.close()


def test_any_other_failure_propagates(tmp_path):
    # A swallow-every-OperationalError change (hfm #1) must fail here: the
    # column is NOT there afterwards, so the error is real.
    path, c = _db(tmp_path)
    c.close()
    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ensure_column(ro, "t", "c", "INTEGER")
    ro.close()
