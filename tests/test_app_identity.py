"""App self-identification on /api/status — cross-domain fleet presence, Layer 0.

Mirror of MeshForge's TestAppIdentityBlock (tests/test_map_http_handler.py).
MeshForge and MeshAnchor serve an identically-shaped /api/status on :5000, so a
cross-domain probe cannot tell whose endpoint it hit without the `app` block.
The 2026-06-23 misread — MeshAnchor's honest_status reporting MeshForge's
confirmation_rate as its own — came from exactly this ambiguity. Here we assert
the MeshAnchor side names itself 'meshanchor' (the negative-parity assertion that
the two apps disambiguate). The byte-identical _build_app_block/_read_deployment_role
helpers are parity-tracked (scripts/parity_check.py).
"""
import json
from io import BytesIO
from unittest.mock import MagicMock

from utils.map_http_handler import MapRequestHandler


class TestAppIdentityBlock:
    def test_build_app_block_names_meshanchor(self):
        from utils._map_status_endpoints import _build_app_block
        from __version__ import __version__ as ver
        block = _build_app_block()
        # name is the disambiguation key, lower-cased from __app_name__.
        assert block["name"] == "meshanchor"
        assert block["repo"] == "meshanchor"
        assert block["version"] == ver
        # host is always a present, non-empty string (best-effort, never raises).
        assert isinstance(block["host"], str) and block["host"]

    def test_read_deployment_role_absent_is_none_not_raise(self):
        # honest_failure_modes #2: an unobservable role must be None, never a
        # forged value, and must never raise out of the status path.
        from utils._map_status_endpoints import _read_deployment_role
        assert _read_deployment_role("definitely-not-an-app-xyz") is None

    def test_server_version_header_names_app(self):
        # Even a HEAD / discloses the app via the Server: header.
        assert MapRequestHandler.server_version.startswith("MeshAnchor")

    def test_serve_status_payload_includes_app_block(self):
        # Wiring: the emitted /api/status JSON carries `app` even with no
        # collector (a warming/degraded server still self-identifies).
        h = MapRequestHandler.__new__(MapRequestHandler)
        h.collector = None
        h.wfile = BytesIO()
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h._send_cors_header = MagicMock()
        h._get_radio_status_summary = lambda: {}
        h._serve_status()
        payload = json.loads(h.wfile.getvalue())
        assert payload["app"]["name"] == "meshanchor"
        assert payload["app"]["version"]
