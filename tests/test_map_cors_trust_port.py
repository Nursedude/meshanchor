"""Ported MeshForge maps-QA hardening (2026-07-05 audit) — CORS tail-anchor +
LAN-trust gate on the fleet log / test-runner endpoints.

MeshAnchor's map was extracted from MeshForge and shared the same
`origin.startswith(prefix)` CORS bug and the same ungated `/fleet/logs` +
`/fleet/run-test`. `/api/radio/message` stays loopback-only here (stricter than
MF) — these tests also pin that it was NOT loosened.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from utils.map_http_handler import (  # noqa: E402
    MapRequestHandler, _origin_allowed, _client_ip_trusted,
)

ALLOWED = ['http://localhost', 'http://127.0.0.1', 'http://192.168.86.']


class TestCorsOriginHardeningPort:
    def test_legit_origins_allowed(self):
        assert _origin_allowed('http://localhost:5000', ALLOWED)
        assert _origin_allowed('http://192.168.86.41', ALLOWED)
        assert _origin_allowed('http://127.0.0.1', ALLOWED)

    def test_subdomain_suffix_bypass_rejected(self):
        assert not _origin_allowed('http://192.168.86.evil.com', ALLOWED)
        assert not _origin_allowed('http://localhost.attacker.example', ALLOWED)
        assert not _origin_allowed('http://127.0.0.10', ALLOWED)
        assert not _origin_allowed('https://192.168.86.41', ALLOWED)
        assert not _origin_allowed('', ALLOWED)


class TestClientTrustPort:
    def test_loopback_and_lan_trusted(self):
        assert _client_ip_trusted('127.0.0.1', ALLOWED)
        assert _client_ip_trusted('::1', ALLOWED)
        assert _client_ip_trusted('192.168.86.99', ALLOWED)

    def test_other_networks_not_trusted(self):
        assert not _client_ip_trusted('10.44.0.5', ALLOWED)
        assert not _client_ip_trusted('192.168.87.1', ALLOWED)

    def test_no_cors_means_loopback_only(self):
        assert _client_ip_trusted('127.0.0.1', None)
        assert not _client_ip_trusted('192.168.86.99', None)

    def _handler(self, client):
        h = MapRequestHandler.__new__(MapRequestHandler)
        h.allowed_origins = ALLOWED
        h.client_address = (client, 5000)
        return h

    def test_reject_if_untrusted_403_for_foreign_net(self):
        h = self._handler('10.44.0.5')
        cap = {}
        h._serve_json = lambda p, status=200: cap.update(status=status)
        assert h._reject_if_untrusted() is True
        assert cap["status"] == 403

    def test_reject_if_untrusted_allows_lan(self):
        h = self._handler('192.168.86.41')
        h._serve_json = lambda p, status=200: None
        assert h._reject_if_untrusted() is False

    def test_radio_tx_stays_loopback_only(self):
        # _is_localhost must remain strict (loopback-only) — NOT loosened to
        # the LAN-trust gate. A LAN client is trusted for fleet endpoints but
        # NOT for radio TX.
        h = self._handler('192.168.86.41')
        assert h._is_localhost() is False       # LAN client is not localhost
        assert h._client_is_trusted() is True   # ...but is LAN-trusted
        h2 = self._handler('127.0.0.1')
        assert h2._is_localhost() is True
