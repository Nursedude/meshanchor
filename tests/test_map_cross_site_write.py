"""State-changing map POSTs refuse a write a hostile PAGE could forge (2026-09-28).

Port of MF's F3 fix (Fable review): POST /api/radio/message was loopback-only
by client IP, then parsed the body as JSON whatever its Content-Type — so a
browser ON this box visiting an attacker's page could key the radio with a
``text/plain`` "simple request" (no preflight). Pinned through a REAL HTTP
server and the real handler; the transmitter is a recorder, never a radio.
"""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

from utils.map_http_handler import MapRequestHandler

LAN = ["http://localhost", "http://127.0.0.1", "http://192.0.2."]


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(MapRequestHandler, "allowed_origins", LAN)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MapRequestHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    sent = []

    def fake_send(text, destination=None):
        sent.append((text, destination))
        return True

    with patch("gateway.meshtastic_protobuf_client.send_text_direct", fake_send):
        yield srv, sent
    srv.shutdown()
    srv.server_close()


def _req(srv, method, path, body, headers):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    try:
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        c.close()


MSG = json.dumps({"text": "csrf probe", "destination": "^all"})


def test_text_plain_from_a_hostile_page_never_keys_the_radio(server):
    srv, sent = server
    status, _, _ = _req(srv, "POST", "/api/radio/message", MSG,
                        {"Content-Type": "text/plain", "Origin": "http://evil.example"})
    assert status == 415
    assert sent == []


def test_json_from_a_hostile_origin_is_refused(server):
    srv, sent = server
    for origin in ("http://evil.example", "null", "http://192.0.2.evil.com"):
        status, _, _ = _req(srv, "POST", "/api/radio/message", MSG,
                            {"Content-Type": "application/json", "Origin": origin})
        assert status == 403, origin
    assert sent == []


@pytest.mark.parametrize("headers", [
    {"Content-Type": "application/json"},                              # curl / scripts
    {"Content-Type": "application/json; charset=utf-8", "Origin": "http://localhost:5000"},
])
def test_legitimate_senders_still_transmit(server, headers):
    srv, sent = server
    status, _, body = _req(srv, "POST", "/api/radio/message", MSG, headers)
    assert status == 200, body
    assert sent == [("csrf probe", None)]


def test_run_test_refuses_a_forged_post(server):
    srv, _ = server
    body = json.dumps({"test": "x"})
    assert _req(srv, "POST", "/fleet/run-test", body,
                {"Content-Type": "text/plain", "Origin": "http://evil.example"})[0] == 415
    assert _req(srv, "POST", "/fleet/run-test", body,
                {"Content-Type": "application/json", "Origin": "http://evil.example"})[0] == 403


def test_cross_box_fleet_dashboard_fire_is_not_broken(server):
    # web/fleet.html on box A fires ${base}/fleet/run-test on box B: a JSON
    # POST, so the browser preflights first. Any Origin do_OPTIONS grants must
    # also pass the write guard, or the dashboard button silently breaks.
    srv, _ = server
    page = "http://192.0.2.41:5000"
    _, hdrs, _ = _req(srv, "OPTIONS", "/fleet/run-test", None,
                      {"Origin": page, "Access-Control-Request-Method": "POST"})
    assert hdrs.get("Access-Control-Allow-Origin") == page
    status, _, body = _req(srv, "POST", "/fleet/run-test", json.dumps({"test": "no-such-test"}),
                           {"Content-Type": "application/json", "Origin": page})
    assert status not in (403, 415), body   # past both gates; the allowlist answers
