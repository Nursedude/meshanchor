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


# ── 2. the rx call sites record the WIRE's slot and reach ──────────────────


def _handler():
    from gateway.meshcore_handler import MeshCoreHandler
    meshcore = SimpleNamespace(
        enabled=True, device_path="/dev/ttyUSB1", baud_rate=115200,
        connection_type="serial", tcp_host="localhost", tcp_port=4000,
        auto_fetch_messages=True, bridge_channels=True, bridge_dms=True,
        simulation_mode=True, channel_poll_interval_sec=5,
        bridge_source_channels=None)
    config = SimpleNamespace(
        meshcore=meshcore,
        meshtastic=SimpleNamespace(host="localhost", port=4403))
    health = MagicMock()
    health.record_error.return_value = "transient"
    h = MeshCoreHandler(config=config, node_tracker=MagicMock(), health=health,
                        stop_event=threading.Event(), stats={"errors": 0},
                        stats_lock=threading.Lock(), message_queue=Queue(maxsize=10))
    h.get_radio_state = lambda refresh=False: {
        "channels": [{"idx": 0, "name": "Public", "hash": "11"},
                     {"idx": 1, "name": "meshanchor", "hash": "9f"}]}
    h._oracle = None
    h._should_bridge = None
    return h


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()


def test_channel_rx_records_the_wire_slot_and_reach_in_the_chat_buffer():
    # CHANNEL_MSG_RECV exactly as meshcore_py 2.3.14 reader.py dispatches it
    # (keys read on meshanchor-server: SNR, channel_idx, path_hash_mode,
    # path_len, sender_timestamp, text, txt_type, type) — no `channel`, no
    # sender key.
    h = _handler()
    ev = SimpleNamespace(type="CHANNEL_MSG_RECV", payload={
        "type": "CHAN", "channel_idx": 1, "path_len": 5, "SNR": -1.25,
        "path_hash_mode": -1, "txt_type": 0, "sender_timestamp": 1790232011,
        "text": "TommyGuns: Good evening."})
    _run(h._on_channel_message(ev))
    entry = h._chat_buffer[-1]
    assert entry["direction"] == "rx"
    assert entry["channel"] == 1            # the wire's slot, from metadata
    assert entry["reach"] == "hops=5 snr=-1.25"
    assert not entry["sender"]              # a channel rx carries no sender key


def test_channel_rx_without_a_slot_records_none_never_public():
    h = _handler()
    ev = SimpleNamespace(type="CHANNEL_MSG_RECV", payload={
        "type": "CHAN", "path_len": 0, "txt_type": 0, "text": "x"})
    _run(h._on_channel_message(ev))
    assert h._chat_buffer[-1]["channel"] is None


def test_dm_rx_records_reach_and_no_slot():
    h = _handler()
    ev = SimpleNamespace(type="CONTACT_MSG_RECV", payload={
        "type": "PRIV", "pubkey_prefix": "3a4b5c6d7e8f", "path_len": 255,
        "SNR": 6.0, "txt_type": 0, "sender_timestamp": 1790232011, "text": "hi"})
    _run(h._on_contact_message(ev))
    entry = h._chat_buffer[-1]
    assert entry["channel"] is None and entry["sender"] == "3a4b5c6d7e8f"
    assert entry["reach"] == "hops=direct snr=6"


# ── 3. a DM rx is a DM, not an unknown slot ────────────────────────────────


def test_dm_rx_renders_dm_not_unknown_slot():
    out = _plain(_format_entry(
        {"ts": 0, "direction": "rx", "channel": None, "sender": "3a4b5c6d7e8f",
         "destination": None, "reach": "hops=direct snr=6", "text": "hi"},
        {0: "Public"}))
    assert "unknown slot" not in out
    assert "DM hops=direct snr=6 3a4b5c6d7e8f: hi" in out


def test_channel_rx_with_no_slot_and_no_sender_still_says_unknown_slot():
    out = _plain(_format_entry({"ts": 0, "direction": "rx", "sender": "",
                                "text": "x"}))
    assert "unknown slot" in out
