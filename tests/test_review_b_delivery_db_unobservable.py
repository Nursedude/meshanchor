"""Review B (non-author, 2026-09-23) — gates for what the tests let die.

Each test here FAILED against the code as committed (005c90ad / 282ed86e) or
against a planted mutant that the existing suites let through:

* a delivery snapshot whose DB read fails THIS call served all-zero counters
  with no witness key -> the Delivery screen read QUIET (hfm #1);
* the chat rx call sites could read the wrong metadata key and every chat /
  meshcore test still passed (the only pin was a source-string grep);
* a DM rx rendered "ch?(unknown slot)" — a DM has no slot by design;
* confirmation_window's protocol filter and its `c > 0` floor could be
  removed without a failure;
* the extraction walked the ring BEFORE the no-confirmable early return
  (differential fuzz: old healthy / new TypeError on a corrupt ring).
"""
import asyncio
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gateway import delivery_counters as dc  # noqa: E402
from utils import active_health_checks_delivery as ahcd  # noqa: E402
from utils import delivery_view as dv  # noqa: E402
from utils.chat_client import _format_entry  # noqa: E402


def _plain(s):
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


# ── 1. a DB read that fails THIS call is UNKNOWN, never QUIET ─────────────


def _failing_counters(tmp_path):
    c = dc.DeliveryCounters(db_path=Path(tmp_path) / "d.db")

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    c._connect = boom
    return c


def test_snapshot_read_failure_carries_a_witness(tmp_path):
    snap = _failing_counters(tmp_path).snapshot()
    assert snap["health"]["db_unobservable"] is True
    assert snap["health"]["preflight_ok"] is None


def test_delivery_screen_reads_unknown_when_the_db_read_failed(tmp_path):
    snap = _failing_counters(tmp_path).snapshot()
    now = time.time()
    q = {"delivered": 1, "failed": 0, "dead_letter": 0, "pending": 0,
         "in_progress": 0, "queue_depth": 0, "max_queue_size": 1000,
         "timestamp": now}
    v = dv.gather(home=str(tmp_path), now=now,
                  unit_resolver=lambda u: ("ok", 1),
                  unit_enabled_fn=lambda u: True,
                  fetcher=lambda url: snap if url.endswith("/delivery") else q)
    assert v.legs[0].status == dv.UNKNOWN
    assert v.headline().startswith("UNKNOWN")
