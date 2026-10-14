"""State-changing map POSTs refuse a write a hostile PAGE could forge (2026-09-28).

Port of MF's F3 fix (Fable review): POST /api/radio/message was loopback-only
by client IP, then parsed the body as JSON whatever its Content-Type — so a
browser ON this box visiting an attacker's page could key the radio with a
``text/plain`` "simple request" (no preflight). Pinned through a REAL HTTP
server and the real handler; the transmitter is a recorder, never a radio.

BIC-3 (2026-10-13, port of MF) put an operator login in FRONT of this layer:
a forged write is refused (401/403/415) before or by it, and a bare loopback
``curl`` no longer transmits. The layer itself is still exercised below with a
real operator session, so it cannot rot behind the login.
"""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

from utils import web_auth
from utils.map_http_handler import MapRequestHandler

LAN = ["http://localhost", "http://127.0.0.1", "http://192.0.2."]


@pytest.fixture
def server(monkeypatch, tmp_path):
    store = web_auth.SessionStore(tmp_path / "web_sessions.json")
    monkeypatch.setattr(MapRequestHandler, "auth_store", store)
    monkeypatch.setattr(MapRequestHandler, "allowed_origins", LAN)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MapRequestHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    sent = []

    def fake_send(text, destination=None):
        sent.append((text, destination))
        return True

    srv.store = store
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
REFUSED = (401, 403, 415)


def _cookie(srv):
    """A real operator session, minted the way a browser gets one."""
    port = srv.server_address[1]
    code = srv.store.start_pairing()
    status, hdrs, body = _req(srv, "POST", "/login", json.dumps({"code": code}),
                              {"Content-Type": "application/json",
                               "Origin": f"http://localhost:{port}"})
    assert status == 200, body
    return hdrs["Set-Cookie"].split(";", 1)[0]


def test_text_plain_from_a_hostile_page_never_keys_the_radio(server):
    srv, sent = server
    status, _, _ = _req(srv, "POST", "/api/radio/message", MSG,
                        {"Content-Type": "text/plain", "Origin": "http://evil.example",
                         "Cookie": _cookie(srv)})
    assert status in REFUSED
    assert sent == []


def test_json_from_a_hostile_origin_is_refused(server):
    srv, sent = server
    cookie = _cookie(srv)
    for origin in ("http://evil.example", "null", "http://192.0.2.evil.com"):
        status, _, _ = _req(srv, "POST", "/api/radio/message", MSG,
                            {"Content-Type": "application/json", "Origin": origin,
                             "Cookie": cookie})
        assert status in REFUSED, origin
    assert sent == []


def test_bare_loopback_curl_no_longer_transmits(server):
    """BIC-3: this exact request was a 'legitimate sender' until 2026-10-13."""
    srv, sent = server
    status, _, _ = _req(srv, "POST", "/api/radio/message", MSG,
                        {"Content-Type": "application/json"})
    assert status == 401
    assert sent == []


def test_legitimate_senders_still_transmit(server):
    srv, sent = server
    headers = {"Content-Type": "application/json; charset=utf-8",
               "Origin": f"http://localhost:{srv.server_address[1]}", "Cookie": _cookie(srv)}
    status, _, body = _req(srv, "POST", "/api/radio/message", MSG, headers)
    assert status == 200, body
    assert sent == [("csrf probe", None)]


def test_run_test_refuses_a_forged_post(server):
    srv, _ = server
    body = json.dumps({"test": "x"})
    cookie = _cookie(srv)
    assert _req(srv, "POST", "/fleet/run-test", body,
                {"Content-Type": "text/plain", "Origin": "http://evil.example",
                 "Cookie": cookie})[0] in REFUSED
    assert _req(srv, "POST", "/fleet/run-test", body,
                {"Content-Type": "application/json", "Origin": "http://evil.example",
                 "Cookie": cookie})[0] in REFUSED


def test_cross_box_fleet_dashboard_fire_needs_a_session_on_that_box(server):
    # web/fleet.html on box A fires ${base}/fleet/run-test on box B. BIC-3
    # (operator decision Q11: sessions are per box): without a session ON B
    # the fire is refused LOUDLY with how to log in — never silently. With one,
    # box A's Origin still passes both gates and the allowlist answers.
    srv, _ = server
    page = "http://192.0.2.41:5000"
    _, hdrs, _ = _req(srv, "OPTIONS", "/fleet/run-test", None,
                      {"Origin": page, "Access-Control-Request-Method": "POST"})
    assert hdrs.get("Access-Control-Allow-Origin") == page
    body = json.dumps({"test": "no-such-test"})
    status, _, resp = _req(srv, "POST", "/fleet/run-test", body,
                           {"Content-Type": "application/json", "Origin": page})
    assert status == 401 and b"meshanchor_web.py pair" in resp
    status, _, resp = _req(srv, "POST", "/fleet/run-test", body,
                           {"Content-Type": "application/json", "Origin": page,
                            "Cookie": _cookie(srv)})
    assert status not in REFUSED, resp   # past every gate; the allowlist answers


@pytest.mark.parametrize("name", ["meshanchor-server", "moc", "node.local", "moc.mf.internal"])
@pytest.mark.parametrize("path", ["/fleet/run-test", "/api/radio/message"])
def test_own_dashboard_opened_by_name_is_not_refused(server, name, path):
    # Fable re-review 2026-09-28 #1: the F3 port dropped same-origin-by-NAME, so
    # the self-box "Run Tests" button 403'd whenever the page was opened as
    # http://<name>:5000. The IP-only cross-box test above could not see it.
    srv, _ = server
    port = srv.server_address[1]
    body = json.dumps({"test": "no-such-test"}) if path == "/fleet/run-test" else MSG
    status, _, resp = _req(srv, "POST", path, body,
                           {"Content-Type": "application/json", "Cookie": _cookie(srv),
                            "Origin": f"http://{name}:{port}", "Host": f"{name}:{port}"})
    assert status not in REFUSED, (name, path, resp)


def test_a_hostile_page_cannot_borrow_the_same_name_rule(server):
    srv, sent = server
    port = srv.server_address[1]
    for origin, host in ((f"http://evil.example:{port}", f"evil.example:{port}"),   # public name
                         (f"http://moc:{port}", f"other:{port}"),                  # names differ
                         (f"http://moc:{port + 1}", f"moc:{port}")):               # port differs
        status, _, _ = _req(srv, "POST", "/api/radio/message", MSG,
                            {"Content-Type": "application/json", "Origin": origin, "Host": host,
                             "Cookie": _cookie(srv)})
        assert status in REFUSED, (origin, host)
    assert sent == []


@pytest.mark.parametrize("ctype", ["application/x-www-form-urlencoded",
                                   "multipart/form-data; boundary=x"])
def test_every_cors_safelisted_type_is_refused(server, ctype):
    # a <form> can send these two without a preflight — the guard must refuse
    # them as it refuses text/plain (Fable re-review #4, mutant M1 survived)
    srv, sent = server
    status, _, _ = _req(srv, "POST", "/api/radio/message", MSG,
                        {"Content-Type": ctype, "Origin": "http://evil.example"})
    assert status in REFUSED
    # and behind a valid login the cross-site layer still names the type
    status, _, _ = _req(srv, "POST", "/api/radio/message", MSG,
                        {"Content-Type": ctype, "Cookie": _cookie(srv),
                         "Origin": f"http://localhost:{srv.server_address[1]}"})
    assert status == 415
    assert sent == []
