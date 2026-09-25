"""Non-author review of the MA truth-sweep port (2026-09-25) — the ports it forced.

1. The default latency probe list: rnsd's shared instance is a UNIX socket
   (TCP 37428 reads DOWN on every healthy box), and meshtasticd_http probed
   4403 twice. MF fixed it first (lead repo); this pins the port.
2. Node Health never hands the operator a raw `sudo systemctl start` from a
   TCP probe (it offered an rnsd restart — the #69 race trigger).
3. Maps > Open: with no graphical session it must not launch a text browser
   inside the TUI; a True from webbrowser.open() is a hand-off, not a window.
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
for p in (str(_SRC), str(_SRC / "launcher_tui"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402


def test_probe_list_has_no_duplicate_port_and_no_tcp_rnsd():
    from utils import latency_monitor as lm
    from utils.ports import MESHTASTICD_WEB_PORT
    ports = [p for _n, _h, p in lm.DEFAULT_SERVICES]
    assert len(ports) == len(set(ports))
    assert ("meshtasticd_http", "localhost", MESHTASTICD_WEB_PORT) in lm.DEFAULT_SERVICES
    assert not any(n == "rnsd" for n, _h, _p in lm.DEFAULT_SERVICES)
    assert any(n == "rnsd" for n, _w in lm.NOT_TCP_PROBED)


def test_node_health_never_prints_a_raw_start_command(monkeypatch, capsys):
    import handlers.node_health as nh
    monkeypatch.setattr(nh, "probe_tcp", lambda h, p, timeout=2.0: (False, 0.0))
    ctx = make_handler_context(dialog=FakeDialog())
    ctx.wait_for_enter = lambda *a, **k: None
    h = nh.NodeHealthHandler()
    h.ctx = ctx
    h._service_latency_probe()
    out = capsys.readouterr().out
    assert "sudo systemctl start" not in out
    assert "CLOSED" in out and "DOWN" not in out and "HEALTHY" not in out
    assert "not TCP-probed" in out and "rnsd" in out
    assert "LISTENING there, not that" in out


def _maps_handler():
    import handlers.meshforge_maps as mm
    ctx = make_handler_context(dialog=FakeDialog())
    ctx.wait_for_enter = lambda *a, **k: None
    h = mm.MeshforgeMapsHandler() if hasattr(mm, "MeshforgeMapsHandler") else next(
        getattr(mm, n)() for n in dir(mm) if n.endswith("Handler") and n != "BaseHandler")
    h.ctx = ctx
    return mm, h


def test_open_without_a_graphical_session_launches_nothing(monkeypatch, capsys):
    mm, h = _maps_handler()
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    called = []
    monkeypatch.setattr(mm.webbrowser, "open", lambda *a, **k: called.append(a) or True)
    h._open_browser()
    out = capsys.readouterr().out
    assert called == [] and "graphical browser is not available here" in out and "Open this in a browser" in out


def test_a_true_from_webbrowser_is_a_handoff_not_a_window(monkeypatch, capsys):
    mm, h = _maps_handler()
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(mm.webbrowser, "open", lambda *a, **k: True)
    h._open_browser()
    out = capsys.readouterr().out
    assert "Handed" in out and "not observable" in out
    assert "Browser launched" not in out
