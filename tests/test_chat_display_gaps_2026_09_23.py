"""Chat pane display gaps (operator's pane, 2026-09-23).

``[16:55:15] ← ? ?: Delta: …`` — the daemon logged idx=0 name=Public
hops=5 snr=-5 for that frame, yet the pane showed no slot and a "?" sender;
``[17:51:55] → ch0 (Public) ?: ola`` — our own sends carried "?".
Pinned: the rx slot comes from metadata['channel'] (CanonicalMessage has no
.channel), the entry carries reach, own sends read "me", and a channel rx
prints no sender placeholder (the text carries the self-reported name).
"""
import os
import sys
from types import SimpleNamespace as N

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.chat_client import _format_entry  # noqa: E402


def _plain(s):
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def test_channel_rx_shows_slot_and_reach_and_no_placeholder():
    out = _plain(_format_entry(
        {"ts": 0, "direction": "rx", "channel": 0, "sender": None,
         "reach": "hops=5 snr=-5", "text": "Delta: https://example"},
        {0: "Public"}))
    assert "ch0 (Public) hops=5 snr=-5" in out
    assert "?" not in out.split("Delta")[0]
    assert out.endswith("· Delta: https://example")


def test_own_send_reads_me():
    out = _plain(_format_entry(
        {"ts": 0, "direction": "tx", "channel": 0, "text": "ola"}, {0: "Public"}))
    assert "ch0 (Public) me: ola" in out


def test_unknown_slot_is_said_not_a_bare_question_mark():
    out = _plain(_format_entry({"ts": 0, "direction": "rx", "text": "x"}))
    assert "unknown slot" in out


def test_dm_rx_without_sender_is_an_honest_unknown():
    out = _plain(_format_entry(
        {"ts": 0, "direction": "rx", "destination": "abcd1234", "text": "hi"}))
    assert "?: hi" in out


class _Handler:
    """Just the chat-buffer surface of MeshCoreHandler."""
    def __init__(self):
        import threading
        self._chat_buffer_lock = threading.Lock()
        self._chat_seq = 0
        from collections import deque
        self._chat_buffer = deque(maxlen=10)


def test_handler_buffer_records_reach():
    from gateway.meshcore_handler import MeshCoreHandler
    h = _Handler()
    MeshCoreHandler.record_chat_message(h, direction="rx", text="t", channel=0,
                                        reach="hops=direct snr=6")
    assert h._chat_buffer[-1]["reach"] == "hops=direct snr=6"
    assert h._chat_buffer[-1]["channel"] == 0


def test_rx_paths_read_the_slot_from_metadata_not_an_attribute():
    # The defect in source form: getattr(msg, "channel", None) is always None.
    root = os.path.join(os.path.dirname(__file__), "..", "src", "gateway")
    for name in ("meshcore_handler.py", "meshcore_supervisor_handler.py"):
        src = open(os.path.join(root, name), encoding="utf-8").read()
        assert 'getattr(msg, "channel", None)' not in src, name
