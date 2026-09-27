"""Gateway traffic capture tells the truth — port of MeshForge 2026-09-26.

Measured on meshanchor-server before the port: last 400 RNS rows = 400 x
source "local", 180 same-second repeats; the traffic log was truncated on
every open. MF commits: fe5c4ef5 (log append), 417a16e9 (sender/direction),
0f0502f4 (single capture), 0511b410 (Radio Health unavailable != 0).
"""
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from monitoring import rns_sniffer as rs  # noqa: E402
from monitoring.packet_dissectors import RNSDissector  # noqa: E402
from monitoring.traffic_models import PacketDirection  # noqa: E402
from monitoring.traffic_storage import TrafficLogger  # noqa: E402

SRC = bytes.fromhex("4b21083c21f84e0a" * 2)
DST = bytes.fromhex("2b86f1b1f4f475aa" * 2)


def test_traffic_log_is_appended_not_truncated(tmp_path):
    log = tmp_path / "traffic.log"
    TrafficLogger(str(log))
    with open(log, "a") as f:
        f.write("12:00:00.000 <-   rns        4b21083c21f84e\n")
    TrafficLogger(str(log))
    text = log.read_text()
    assert "4b21083c21f84e" in text
    assert text.count("MESHFORGE TRAFFIC LOG") + text.count("MESHANCHOR TRAFFIC LOG") >= 2
    assert f"pid={os.getpid()}" in text


def test_unknown_source_is_empty_not_local():
    assert RNSDissector().dissect(None, {"protocol": "rns"}).source == ""


def test_sender_and_direction_reach_the_capture():
    seen = []

    class _Insp:
        def capture(self, data, metadata):
            seen.append(metadata)

    class _Sniffer:
        def register_callback(self, cb):
            self.cb = cb

    sniffer = _Sniffer()
    with patch.object(rs, "get_traffic_inspector", lambda: _Insp()), \
         patch.object(rs, "get_rns_sniffer", lambda: sniffer):
        rs.integrate_with_traffic_inspector()
        sniffer.cb(rs.RNSPacketInfo(packet_type=rs.RNSPacketType.ANNOUNCE,
                                    destination_hash=DST, source_hash=SRC,
                                    direction="outbound"))
    packet = RNSDissector().dissect(None, seen[0])
    assert packet.source == SRC.hex()
    assert packet.direction == PacketDirection.OUTBOUND


def _bridge_announce(hooks_installed):
    from gateway import rns_bridge
    fake_rns = types.ModuleType("RNS")
    fake_rns.Transport = SimpleNamespace(has_path=lambda h: False, hops_to=lambda h: 0)
    sniffer = MagicMock(_running=True, _hooks_installed=hooks_installed)
    host = SimpleNamespace(node_tracker=MagicMock())
    with patch.dict("sys.modules", {"RNS": fake_rns}), \
         patch.object(rns_bridge, "HAS_RNS_SNIFFER", True), \
         patch.object(rns_bridge, "get_rns_sniffer", return_value=sniffer), \
         patch.object(rns_bridge, "UnifiedNode"):
        rns_bridge.RNSMeshtasticBridge._on_rns_announce(host, DST, None, b"")
    return sniffer


def test_bridge_does_not_duplicate_the_sniffers_own_capture():
    assert not _bridge_announce(hooks_installed=True)._store_packet.called


def test_bridge_still_captures_when_the_sniffer_has_no_hooks():
    _bridge_announce(hooks_installed=False)._store_packet.assert_called_once()


def test_unavailable_http_api_is_not_zero_nodes():
    """meshtasticd never serves /json/nodes (#76): unavailable stays None."""
    client = MagicMock(is_available=False)
    cli = MagicMock()
    cli.get_nodes.return_value = MagicMock(success=True,
                                           output="\n".join(f"!n{i:04d} N" for i in range(340)))
    svc = MagicMock(available=True, state=MagicMock(value="available"), message="OK")
    import importlib
    with patch.dict("sys.modules", {
        "utils.service_check": MagicMock(check_service=MagicMock(return_value=svc),
                                         check_port=MagicMock(return_value=True)),
        "utils.meshtastic_http": MagicMock(get_http_client=MagicMock(return_value=client)),
        "core.meshtastic_cli": MagicMock(get_cli=MagicMock(return_value=cli)),
    }):
        import commands.hardware as hw
        importlib.reload(hw)
        try:
            result = hw.get_radio_health()
        finally:
            importlib.reload(hw)
    assert result.data["http_nodes"] is None
    assert not [w for w in result.data["warnings"] if "mismatch" in w.lower()]
