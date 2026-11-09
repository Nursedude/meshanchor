"""The suite must never open the operator's live node_history.db.

``NodeHistoryDB()`` without ``db_path`` resolves the home through
``utils.node_history.get_real_user_home``; the conftest
``_OPERATOR_STORE_MODULES`` gate must redirect THAT import (2026-11-09: the
gate listed only ``utils.map_data_collector``, which never covered it).
"""

from utils import node_history
from utils.paths import get_real_user_home


def test_default_node_history_path_is_redirected(_isolate_operator_data_stores):
    root = _isolate_operator_data_stores
    resolved = node_history.get_real_user_home()
    assert resolved == root, (
        "utils.node_history.get_real_user_home is NOT redirected — a "
        "NodeHistoryDB() in a test would open the live node_history.db")


def test_default_db_lands_under_isolated_root(_isolate_operator_data_stores):
    db = node_history.NodeHistoryDB()
    try:
        assert str(db.db_path).startswith(str(_isolate_operator_data_stores))
        assert not str(db.db_path).startswith(str(get_real_user_home()))
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()
