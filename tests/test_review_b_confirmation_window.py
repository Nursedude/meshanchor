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


# ── 4. confirmation_window: the filters the mutants removed unnoticed ──────


def _ring(n_conf_rns, n_fail_mesh):
    return ([{"state": "confirmed", "protocol": "rns"}] * n_conf_rns
            + [{"state": "dropped", "protocol": "meshtastic",
                "drop_reason": "retries_exhausted"}] * n_fail_mesh)


def test_window_ignores_terminals_of_unconfirmable_protocols():
    # Live shape on meshanchor-server: only rns confirms; meshtastic drops
    # (retries_exhausted 51) share the same ring. They must not count as
    # rns failures.
    snap = {"state_by_protocol": {"confirmed": {"rns": 9}},
            "recent_terminal": _ring(20, 40)}
    w = ahcd.confirmation_window(snap)
    assert (w["confirmed"], w["failed"]) == (20, 0)
    r = ahcd.check_delivery_confirmation_stall(snap=snap)
    assert r.healthy, r.reason


def test_zero_confirmations_is_not_a_confirmable_protocol():
    snap = {"state_by_protocol": {"confirmed": {"rns": 0, "meshcore": True}},
            "recent_terminal": _ring(0, 40)}
    assert ahcd.confirmation_window(snap)["confirmable"] == set()
    assert ahcd.check_delivery_confirmation_stall(snap=snap).reason == "no_confirmable_protocol"


def test_no_confirmable_protocol_never_touches_a_corrupt_ring():
    # Pre-extraction order: early return before the ring walk. An unhashable
    # protocol in the ring must not turn "no_confirmable_protocol" into a
    # check_exception (differential fuzz against 282ed86e~1).
    snap = {"state_by_protocol": {"confirmed": {}},
            "recent_terminal": [{"state": "dropped", "protocol": ["x"]}]}
    assert ahcd.confirmation_window(snap)["confirmed"] == 0
    assert ahcd.check_delivery_confirmation_stall(snap=snap).reason == "no_confirmable_protocol"
