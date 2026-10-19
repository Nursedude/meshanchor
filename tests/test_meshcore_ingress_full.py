"""A MeshCore message lost because the handler->bridge queue is full was a
WARNING per message and counted nowhere (MA sibling of MF ff94052a). It is a
message: counted (meshcore_ingress_dropped_queue_full, per path) and said at
WARNING once per 5 min, DEBUG after — a stalled bridge must not flood the
journal, and must not be invisible in the stats either.
"""
import asyncio
import logging
from queue import Queue
from types import SimpleNamespace

from tests.test_meshcore_handler import (  # noqa: F401 — fixture re-export
    handler, mock_config, mock_health, mock_node_tracker)


def _event(text):
    return SimpleNamespace(type='CHANNEL_MSG_RECV', payload={
        'text': text, 'sender': 'node789', 'destination': None,
        'is_channel': True, 'channel': 1})


def test_a_full_ingress_queue_is_counted_and_warned_once(handler, caplog):  # noqa: F811
    full = Queue(maxsize=1)
    full.put_nowait(object())
    handler._message_queue = full
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(handler._connect())
        with caplog.at_level(logging.DEBUG):
            loop.run_until_complete(handler._on_channel_message(_event("one")))
            loop.run_until_complete(handler._on_channel_message(_event("two")))
    finally:
        loop.close()
    assert handler.stats.get('meshcore_ingress_dropped_queue_full') == 2
    assert handler.stats.get('meshcore_ingress_dropped_queue_full_event') == 2
    warns = [r for r in caplog.records if r.levelno == logging.WARNING
             and "queue full" in r.getMessage()]
    assert len(warns) == 1, [r.getMessage() for r in warns]


# No poll-path test, deliberately: the poll leg calls
# commands.get_channel_messages / get_messages, and the installed meshcore_py
# has NEITHER (only get_msg — measured on meshanchor-server's venv,
# 2026-10-18), so that leg fetches nothing in production. A test that mocks a
# method the library lacks would pin a path that cannot run (QUEUED finding).


def test_dm_notice_drops_share_the_rate_limited_voice(handler, caplog):  # noqa: F811
    from gateway._meshcore_ingress import note_ingress_queue_full, warn_ingress_full
    with caplog.at_level(logging.DEBUG):
        note_ingress_queue_full(handler, "event")
        warn_ingress_full(handler, "DM notice dropped")
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1


def test_surfaces_name_the_counter(capsys):
    from gateway import bridge_cli
    from launcher_tui.handlers.meshcore import MeshCoreHandler as TuiHandler
    stats = {"meshcore_ingress_dropped_queue_full": 4,
             "messages_meshcore_to_mesh": 1}
    assert any("queue full): 4" in l for l in TuiHandler._bridged_lines(stats))
    bridge_cli.print_status({"running": True, "statistics": dict(
        stats, messages_mesh_to_rns=0, messages_rns_to_mesh=0)})
    assert "MeshCore ingress dropped (queue full): 4" in capsys.readouterr().out
