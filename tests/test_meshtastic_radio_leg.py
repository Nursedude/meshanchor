"""mqtt_bridge "Meshtastic: connected" while meshtasticd is stopped (2026-10-02).

Port of MeshForge 5440d021 (tests adapted: get_status lives in
BridgeHealthMixin here; no multi-bridge printer).

Measured on moc3: meshtasticd stopped 19:21-19:51 HST for an RF drill; the
gateway printed "Meshtastic: connected" every 30 s throughout, because in
mqtt_bridge mode ``meshtastic_connected`` is the MQTT BROKER session and
mosquitto stayed up. Its own journal said "Connection refused" on /toradio
at the same time. These tests pin the radio side as a separate reading.
"""

import os
import socket
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

RNS_UP = {"running": True, "meshtastic_connected": True, "rns_connected": True}


def _render(status, capsys):
    from gateway.bridge_cli import print_status
    print_status(status)
    return capsys.readouterr().out


class TestStatusLineReadsTheRadio:

    def test_broker_up_radio_down_reads_disconnected(self, capsys):
        out = _render(dict(RNS_UP, meshtastic_radio={
            "reachable": False, "endpoint": "localhost:9443"}), capsys)
        assert "Meshtastic: connected" not in out, out
        assert ("Meshtastic: disconnected (MQTT broker up; meshtasticd not "
                "answering at localhost:9443)") in out

    def test_both_up_reads_connected(self, capsys):
        out = _render(dict(RNS_UP, meshtastic_radio={
            "reachable": True, "endpoint": "localhost:9443"}), capsys)
        assert "Meshtastic: connected\n" in out

    def test_unobservable_radio_is_unknown_not_a_fault_or_health(self, capsys):
        out = _render(dict(RNS_UP, meshtastic_radio={
            "reachable": None, "endpoint": "localhost:9443"}), capsys)
        line = [ln for ln in out.splitlines() if ln.startswith("Meshtastic:")][0]
        assert "UNKNOWN" in line and "disconnected" not in line, line

    def test_broker_down_stays_disconnected(self, capsys):
        out = _render(dict(RNS_UP, meshtastic_connected=False, meshtastic_radio={
            "reachable": True, "endpoint": "localhost:9443"}), capsys)
        assert "Meshtastic: disconnected\n" in out

    def test_handler_without_radio_read_keeps_legacy_line(self, capsys):
        out = _render(dict(RNS_UP), capsys)
        assert "Meshtastic: connected\n" in out


def _handler(port):
    from gateway.mqtt_bridge_handler import MQTTBridgeHandler
    h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
    h.config = SimpleNamespace(meshtastic=SimpleNamespace(
        host="127.0.0.1", http_port=port))
    return h


class TestRadioReachabilityMeasures:
    """Real sockets: the read must be able to say both True and False."""

    def test_listening_port_reads_reachable(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        try:
            port = srv.getsockname()[1]
            assert _handler(port).radio_reachability() == {
                "reachable": True, "endpoint": f"127.0.0.1:{port}"}
        finally:
            srv.close()

    def test_closed_port_reads_unreachable(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # nothing listens here now
        assert _handler(port).radio_reachability()["reachable"] is False


class TestGetStatusCarriesTheRadio:
    """Consumer path: the bridge's status dict is what print_status reads."""

    def _status(self, handler):
        from gateway.bridge_health_mixin import BridgeHealthMixin
        fake = MagicMock()
        fake.stats = {"start_time": None}
        fake._mesh_handler = handler
        fake._meshcore_handler = None
        return BridgeHealthMixin.get_status(fake)

    def test_mqtt_handler_status_includes_radio(self):
        h = MagicMock()
        h.is_connected = True
        h.radio_reachability.return_value = {
            "reachable": False, "endpoint": "localhost:9443"}
        st = self._status(h)
        assert st["meshtastic_connected"] is True
        assert st["meshtastic_radio"] == {
            "reachable": False, "endpoint": "localhost:9443"}

    def test_handler_without_the_read_omits_the_key(self):
        h = MagicMock(spec=["is_connected"])
        h.is_connected = True
        assert "meshtastic_radio" not in self._status(h)

    def test_a_raising_read_is_unknown_not_dropped(self):
        h = MagicMock()
        h.is_connected = True
        h.radio_reachability.side_effect = OSError("boom")
        assert self._status(h)["meshtastic_radio"]["reachable"] is None
