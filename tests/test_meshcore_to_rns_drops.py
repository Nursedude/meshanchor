"""MeshCore->RNS is single-shot (no retry queue). A message that did not go
out was a DEBUG line only — and when RNS was DISCONNECTED the leg was skipped
with no line at all. Operator acceptance (2026-10-18): a MeshCore->RNS message
that doesn't go out is counted and said once at WARNING (rate-limited), never
only a DEBUG line. For RNS, DISABLED means init failed permanently (a drop);
an empty destination list is a MeshCore-only configuration (counted, INFO
once) unless gateway.json failed to load (a drop: destinations unknown).
"""
import logging
from unittest.mock import patch

from tests.test_rns_bridge import bridge  # noqa: F401 — fixture re-export

DESTS = ["3dfbdb5d24c6de195ae4f3c0f56b5ea5"]


def _mc(content="mc broadcast"):
    from gateway.rns_bridge import BridgedMessage
    return BridgedMessage(source_network="meshcore", source_id="!mc01",
                          destination_id=None, content=content,
                          is_broadcast=True)


def _triplet(b):
    s = b.stats
    return (s.get("meshcore_to_rns_attempted", 0),
            s.get("messages_meshcore_to_rns", 0),
            s.get("meshcore_to_rns_dropped", 0))


def _run(b, send=True, rns_state="healthy", dests=DESTS,
         connected=True):  # noqa: F811
    from unittest.mock import MagicMock
    from gateway.bridge_health import SubsystemState
    if rns_state == "healthy":
        rns_state = SubsystemState.HEALTHY  # a real state, never a mock
    b.health.get_subsystem_state.return_value = rns_state
    b.config.load_error = None  # the fixture's config is a MagicMock
    b._connected_rns = connected
    b._lxmf_source = MagicMock(name="lxmf_source") if connected else None
    b.config.rns.get_lxmf_destinations.return_value = dests
    with patch.object(b, "send_to_rns", return_value=send), \
            patch.object(b, "send_to_meshtastic", return_value=True):
        b._process_meshcore_to_bridge(_mc())


def test_delivered_counts_attempted_and_delivered(bridge):  # noqa: F811
    _run(bridge, send=True)
    assert _triplet(bridge) == (1, 1, 0)


def test_unreachable_is_counted_and_warned_once(bridge, caplog):  # noqa: F811
    with caplog.at_level(logging.DEBUG):
        _run(bridge, send=False)
        _run(bridge, send=False)
    assert _triplet(bridge) == (2, 0, 2)
    assert bridge.stats["meshcore_to_rns_dropped_unreachable"] == 2
    warns = [r for r in caplog.records if r.levelno == logging.WARNING
             and "MC→RNS" in r.getMessage()]
    assert len(warns) == 1, [r.getMessage() for r in warns]


def test_no_destinations_is_a_configuration_not_a_drop(bridge, caplog):  # noqa: F811
    """A MeshCore-only box: counted, said once at INFO, never an alarm."""
    with caplog.at_level(logging.DEBUG):
        _run(bridge, send=True, dests=[])
        _run(bridge, send=True, dests=[])
    assert _triplet(bridge) == (0, 0, 0)
    assert bridge.stats["meshcore_to_rns_not_configured"] == 2
    infos = [r for r in caplog.records if "MC→RNS" in r.getMessage()]
    assert len(infos) == 1 and infos[0].levelno == logging.INFO


def test_rns_down_is_counted_not_silent(bridge, caplog):  # noqa: F811
    from gateway.bridge_health import SubsystemState
    with caplog.at_level(logging.WARNING):
        _run(bridge, rns_state=SubsystemState.DISCONNECTED)
    assert _triplet(bridge) == (1, 0, 1)
    assert bridge.stats["meshcore_to_rns_dropped_rns_down"] == 1
    assert any("MC→RNS" in r.getMessage() for r in caplog.records)


def test_rns_failed_permanently_is_a_drop_not_silence(bridge):  # noqa: F811
    """Review F2: for RNS, DISABLED means init failed permanently."""
    from gateway.bridge_health import SubsystemState
    _run(bridge, rns_state=SubsystemState.DISABLED)
    assert _triplet(bridge) == (1, 0, 1)
    assert bridge.stats["meshcore_to_rns_dropped_rns_failed"] == 1


def test_a_drop_tells_the_health_monitor_too(bridge):  # noqa: F811
    """Review F1: one fact, two consumers — stats AND bridge health."""
    _run(bridge, send=False)
    bridge.health.record_message_failed.assert_any_call(
        "meshcore_to_rns", requeued=False)


def test_not_connected_reads_rns_down_not_unreachable(bridge):  # noqa: F811
    """Review F3: the lag between a disconnect and the state flip."""
    _run(bridge, send=False, connected=False)
    assert bridge.stats.get("meshcore_to_rns_dropped_rns_down") == 1
    assert "meshcore_to_rns_dropped_unreachable" not in bridge.stats


def test_a_raise_after_delivery_is_not_also_a_drop(bridge):  # noqa: F811
    def boom(leg):
        if leg == "meshcore_to_rns":  # only the RNS leg's record raises
            raise RuntimeError("hiccup")
    bridge.health.record_message_sent.side_effect = boom
    _run(bridge, send=True)
    assert _triplet(bridge) == (1, 1, 0)


def test_cli_shows_the_meshcore_to_rns_line(capsys):
    from gateway import bridge_cli
    bridge_cli.print_status({"running": True, "statistics": {
        "messages_mesh_to_rns": 0, "messages_rns_to_mesh": 0,
        "meshcore_to_rns_attempted": 5, "messages_meshcore_to_rns": 3,
        "meshcore_to_rns_dropped": 2, "meshcore_to_rns_dropped_rns_down": 2}})
    out = capsys.readouterr().out
    assert "MC->R attempted/delivered/dropped: 5/3/2" in out
    assert "rns_down=2" in out


def test_tui_shows_the_triplet_even_when_every_message_dropped():
    from launcher_tui.handlers.meshcore import MeshCoreHandler
    lines = "\n".join(MeshCoreHandler._bridged_lines(
        {"meshcore_to_rns_attempted": 3, "meshcore_to_rns_dropped": 3}))
    assert "attempted/delivered/dropped: 3/0/3" in lines
    assert MeshCoreHandler._bridged_lines({}) == []


def test_a_failed_config_load_is_a_drop_not_not_configured(bridge):  # noqa: F811
    bridge.config.load_error = "JSONDecodeError: Expecting value"
    bridge.config.rns.get_lxmf_destinations.return_value = []
    bridge.health.get_subsystem_state.return_value = __import__(
        "gateway.bridge_health", fromlist=["x"]).SubsystemState.HEALTHY
    with patch.object(bridge, "send_to_meshtastic", return_value=True):
        bridge._process_meshcore_to_bridge(_mc())
    assert _triplet(bridge) == (1, 0, 1)
    assert bridge.stats["meshcore_to_rns_dropped_config_unreadable"] == 1
    assert "meshcore_to_rns_not_configured" not in bridge.stats
