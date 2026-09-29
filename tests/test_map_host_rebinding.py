"""The trusted-read gate refuses a DNS-rebinding page (port of MF F2, 2026-09-28).

A LAN browser on ``http://evil.example:5000`` whose name the attacker re-points
at this box makes SAME-ORIGIN reads from the victim's trusted address; the IP
gate admits them. The Host header is the one thing the attacker cannot choose,
so the gate also requires it to be an IP literal or a local-only name.

Pinned through a real HTTP server on ``/fleet/logs`` WITHOUT a unit: past the
gate it answers 400 "unit not allowlisted" before any journal read, refused it
answers 403 — so no test path reads this box's journals.
"""
import http.client
import threading
from http.server import ThreadingHTTPServer

import pytest

from utils import _map_trust_gate as tg
from utils.map_http_handler import MapRequestHandler

LAN = ["http://localhost", "http://127.0.0.1", "http://192.0.2."]


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(MapRequestHandler, "allowed_origins", LAN)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MapRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _get(srv, path, host):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    try:
        c.putrequest("GET", path, skip_host=True)
        if host is not None:
            c.putheader("Host", host)
        c.endheaders()
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


@pytest.mark.parametrize("host", ["evil.example:5000", "evil.example",
                                  "192.0.2.7.evil.example:5000", "moc.example.com",
                                  "", "a:b:c"])
def test_rebinding_host_is_refused(server, host):
    status, body = _get(server, "/fleet/logs", host)
    assert status == 403, (host, body)
    assert b"rebinding" in body


@pytest.mark.parametrize("host", [None, "127.0.0.1:5000", "localhost:5000", "localhost",
                                  "meshanchor-server:5000", "moc.mf.internal:5000",
                                  "node.local", "[::1]:5000", "192.0.2.41:5000", "LOCALHOST."])
def test_legitimate_hosts_pass_the_gate(server, host):
    status, body = _get(server, "/fleet/logs", host)
    assert status == 400 and b"not allowlisted" in body, (host, body)


def test_untrusted_ip_still_refused_first():
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.headers = {"Host": "127.0.0.1:5000"}
    h.client_address = ("203.0.113.9", 5000)
    h.allowed_origins = LAN
    served = []
    h._serve_json = lambda payload, status=200: served.append((status, payload))
    assert h._reject_if_untrusted() is True
    assert served[0][0] == 403 and served[0][1]["client"] == "203.0.113.9"


def test_host_rule_uses_the_local_name_vocabulary():
    for name in ("moc", "x.local", "x.home.arpa", "x.internal", "x.local.mesh"):
        assert tg._host_header_trusted(name + ":5000") is tg._local_only_name(name) is True
    assert tg._host_header_trusted("evil.example:5000") is tg._local_only_name("evil.example") is False
