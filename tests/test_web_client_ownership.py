"""
Tests for MeshForge web client ownership architecture.

Validates that MeshForge correctly serves the Meshtastic web client
from disk and routes API calls through the multiplexed proxy.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────
# Static file serving from disk
# ─────────────────────────────────────────────────────────────────


class TestMeshWebClientServing:
    """Test _serve_mesh_web_client() static file handler."""

    def _make_handler(self, path, web_dir=None, api_proxy='_default_mock_'):
        """Create a minimal mock handler for testing.

        Args:
            api_proxy: Set to None to test redirect behavior (proxy disabled).
                       Default provides a mock proxy for testing serving behavior.
        """
        from utils.map_http_handler import MapRequestHandler, MESHTASTICD_WEB_DIR

        handler = MagicMock(spec=MapRequestHandler)
        handler.path = path
        # Default to a mock proxy so tests exercise the serving path.
        # Pass api_proxy=None explicitly to test the redirect behavior.
        if api_proxy == '_default_mock_':
            handler.api_proxy = MagicMock()
        else:
            handler.api_proxy = api_proxy
        handler.wfile = MagicMock()
        handler.headers = {}
        handler.client_address = ('127.0.0.1', 12345)

        # Bind the real methods
        handler._serve_mesh_web_client = MapRequestHandler._serve_mesh_web_client.__get__(handler)
        handler._serve_mesh_client_unavailable = MapRequestHandler._serve_mesh_client_unavailable.__get__(handler)
        handler._rewrite_mesh_html = MapRequestHandler._rewrite_mesh_html
        handler._send_cors_header = MagicMock()
        handler._proxy_json = MagicMock()
        handler._proxy_fromradio = MagicMock()
        handler._proxy_toradio = MagicMock()

        return handler

    def test_mesh_root_serves_index(self, tmp_path):
        """GET /mesh/ should serve index.html from disk."""
        # Create a fake web dir with index.html
        index = tmp_path / "index.html"
        index.write_text("<html><body>Meshtastic</body></html>")

        handler = self._make_handler('/mesh/')

        with patch('utils.map_http_handler.MESHTASTICD_WEB_DIR', str(tmp_path)):
            handler._serve_mesh_web_client()

        handler.send_response.assert_called_with(200)
        handler.wfile.write.assert_called_once()
        written = handler.wfile.write.call_args[0][0]
        assert b'Meshtastic' in written

    def test_mesh_no_trailing_slash(self, tmp_path):
        """GET /mesh should also serve index.html."""
        index = tmp_path / "index.html"
        index.write_text("<html>OK</html>")

        handler = self._make_handler('/mesh')

        with patch('utils.map_http_handler.MESHTASTICD_WEB_DIR', str(tmp_path)):
            handler._serve_mesh_web_client()

        handler.send_response.assert_called_with(200)

    def test_mesh_static_asset(self, tmp_path):
        """GET /mesh/static/app.js should serve from disk."""
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        js_file = static_dir / "app.js"
        js_file.write_text("console.log('hello');")

        handler = self._make_handler('/mesh/static/app.js')

        with patch('utils.map_http_handler.MESHTASTICD_WEB_DIR', str(tmp_path)):
            handler._serve_mesh_web_client()

        handler.send_response.assert_called_with(200)
        written = handler.wfile.write.call_args[0][0]
        assert b"console.log" in written

    def test_mesh_api_fromradio_routes_to_proxy(self, tmp_path):
        """GET /mesh/api/v1/fromradio should route to multiplexed proxy."""
        handler = self._make_handler('/mesh/api/v1/fromradio')

        with patch('utils.map_http_handler.MESHTASTICD_WEB_DIR', str(tmp_path)):
            handler._serve_mesh_web_client()

        handler._proxy_fromradio.assert_called_once()
        handler.wfile.write.assert_not_called()

    def test_mesh_api_toradio_routes_to_proxy(self, tmp_path):
        """GET /mesh/api/v1/toradio should route to proxy."""
        handler = self._make_handler('/mesh/api/v1/toradio')

        with patch('utils.map_http_handler.MESHTASTICD_WEB_DIR', str(tmp_path)):
            handler._serve_mesh_web_client()

        handler._proxy_toradio.assert_called_once()

    def test_mesh_json_nodes_routes_to_proxy(self, tmp_path):
        """GET /mesh/json/nodes should route to sanitized proxy."""
        handler = self._make_handler('/mesh/json/nodes')

        with patch('utils.map_http_handler.MESHTASTICD_WEB_DIR', str(tmp_path)):
            handler._serve_mesh_web_client()

        handler._proxy_json.assert_called_once_with('/json/nodes')

    def test_mesh_json_report_routes_to_proxy(self, tmp_path):
        """GET /mesh/json/report should route to sanitized proxy."""
        handler = self._make_handler('/mesh/json/report')

        with patch('utils.map_http_handler.MESHTASTICD_WEB_DIR', str(tmp_path)):
            handler._serve_mesh_web_client()

        handler._proxy_json.assert_called_once_with('/json/report')

    def test_path_traversal_rejected(self, tmp_path):
        """Path traversal attempts should be rejected."""
        index = tmp_path / "index.html"
        index.write_text("<html>OK</html>")

        handler = self._make_handler('/mesh/../../../etc/passwd')

        with patch('utils.map_http_handler.MESHTASTICD_WEB_DIR', str(tmp_path)):
            handler._serve_mesh_web_client()

        handler.send_error.assert_called_with(400, "Invalid path")

    def test_missing_web_dir_shows_unavailable(self):
        """Missing web dir should show helpful unavailable page."""
        handler = self._make_handler('/mesh/')

        with patch('utils.map_http_handler.MESHTASTICD_WEB_DIR', '/nonexistent/path'):
            handler._serve_mesh_web_client()

        # Should serve the unavailable page (200 with helpful HTML)
        handler.send_response.assert_called_with(200)
        written = handler.wfile.write.call_args[0][0]
        assert b'not found' in written.lower() or b'meshtasticd' in written.lower()

    def test_spa_fallback_serves_index(self, tmp_path):
        """Non-file paths should get index.html (React router)."""
        index = tmp_path / "index.html"
        index.write_text("<html>SPA</html>")

        handler = self._make_handler('/mesh/some/react/route')

        with patch('utils.map_http_handler.MESHTASTICD_WEB_DIR', str(tmp_path)):
            handler._serve_mesh_web_client()

        handler.send_response.assert_called_with(200)
        written = handler.wfile.write.call_args[0][0]
        assert b'SPA' in written

    def test_html_gets_no_cache_header(self, tmp_path):
        """HTML files should have no-cache header."""
        index = tmp_path / "index.html"
        index.write_text("<html>OK</html>")

        handler = self._make_handler('/mesh/')

        with patch('utils.map_http_handler.MESHTASTICD_WEB_DIR', str(tmp_path)):
            handler._serve_mesh_web_client()

        # Verify Cache-Control: no-cache was set for HTML
        cache_calls = [
            c for c in handler.send_header.call_args_list
            if c[0][0] == 'Cache-Control'
        ]
        assert any('no-cache' in str(c) for c in cache_calls)

    def test_js_gets_cache_header(self, tmp_path):
        """JS files should have caching header."""
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        js_file = static_dir / "app.js"
        js_file.write_text("// app")

        handler = self._make_handler('/mesh/static/app.js')

        with patch('utils.map_http_handler.MESHTASTICD_WEB_DIR', str(tmp_path)):
            handler._serve_mesh_web_client()

        cache_calls = [
            c for c in handler.send_header.call_args_list
            if c[0][0] == 'Cache-Control'
        ]
        assert any('max-age' in str(c) for c in cache_calls)

    def test_base_href_injection(self, tmp_path):
        """HTML should have <base href="/mesh/"> for SPA subpath serving."""
        index = tmp_path / "index.html"
        index.write_text("<html><head></head><body>OK</body></html>")

        handler = self._make_handler('/mesh/')

        with patch('utils.map_http_handler.MESHTASTICD_WEB_DIR', str(tmp_path)):
            handler._serve_mesh_web_client()

        written = handler.wfile.write.call_args[0][0]
        assert b'<base href="/mesh/">' in written
        # Old fragile JS injection should NOT be present
        assert b'window.onerror' not in written
        assert b'__MESHFORGE_PROXY__' not in written

    def test_mesh_redirects_to_native_when_proxy_disabled(self):
        """GET /mesh/ should redirect to native :9443 when API proxy is off."""
        handler = self._make_handler('/mesh/', api_proxy=None)
        handler.headers = {'Host': '192.168.1.100:5000'}

        handler._serve_mesh_web_client()

        handler.send_response.assert_called_with(302)
        location_calls = [
            c for c in handler.send_header.call_args_list
            if c[0][0] == 'Location'
        ]
        assert len(location_calls) == 1
        assert ':9443' in location_calls[0][0][1]

    def test_base_href_not_duplicated(self, tmp_path):
        """If HTML already has a <base> tag, don't inject another one."""
        index = tmp_path / "index.html"
        index.write_text('<html><head><base href="/"></head><body>OK</body></html>')

        handler = self._make_handler('/mesh/')

        with patch('utils.map_http_handler.MESHTASTICD_WEB_DIR', str(tmp_path)):
            handler._serve_mesh_web_client()

        written = handler.wfile.write.call_args[0][0]
        assert written.count(b'<base') == 1  # Only the original, no duplication


# ─────────────────────────────────────────────────────────────────
# Port lockdown (iptables)
# ─────────────────────────────────────────────────────────────────


class TestPortLockdown:
    """Test lock_port_external() and unlock_port_external()."""

    @patch('subprocess.run')
    def test_lock_port_adds_rule(self, mock_run):
        """lock_port_external should add iptables rule."""
        from utils.service_check import lock_port_external

        # First call (check) returns 1 (rule doesn't exist)
        # Second call (add) returns 0 (success)
        mock_run.side_effect = [
            MagicMock(returncode=1),  # -C check: not found
            MagicMock(returncode=0),  # -A add: success
        ]

        ok, msg = lock_port_external(9443)

        assert ok is True
        assert "locked" in msg.lower() or "blocked" in msg.lower()
        assert mock_run.call_count == 2

    @patch('subprocess.run')
    def test_lock_port_idempotent(self, mock_run):
        """lock_port_external should be idempotent."""
        from utils.service_check import lock_port_external

        # Check returns 0 (rule already exists)
        mock_run.return_value = MagicMock(returncode=0)

        ok, msg = lock_port_external(9443)

        assert ok is True
        assert "already" in msg.lower()
        assert mock_run.call_count == 1  # Only the check, no add

    @patch('subprocess.run')
    def test_lock_port_custom_port(self, mock_run):
        """lock_port_external should work with custom port."""
        from utils.service_check import lock_port_external

        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
        ]

        ok, msg = lock_port_external(8080)

        assert ok is True
        # Verify the port was in the command
        add_call = mock_run.call_args_list[1]
        assert '8080' in add_call[0][0]

    @patch('subprocess.run', side_effect=FileNotFoundError)
    def test_lock_port_no_iptables(self, mock_run):
        """lock_port_external should handle missing iptables."""
        from utils.service_check import lock_port_external

        ok, msg = lock_port_external()

        assert ok is False
        assert "not found" in msg.lower()

    @patch('subprocess.run')
    def test_unlock_port(self, mock_run):
        """unlock_port_external should remove iptables rule."""
        from utils.service_check import unlock_port_external

        mock_run.return_value = MagicMock(returncode=0)

        ok, msg = unlock_port_external(9443)

        assert ok is True
        assert "unlocked" in msg.lower() or "restored" in msg.lower()

    @patch('subprocess.run')
    def test_unlock_port_already_unlocked(self, mock_run):
        """unlock_port_external should succeed even if rule doesn't exist."""
        from utils.service_check import unlock_port_external

        mock_run.return_value = MagicMock(returncode=1)  # Rule not found

        ok, msg = unlock_port_external(9443)

        assert ok is True  # Still success — desired state achieved


# ─────────────────────────────────────────────────────────────────
# proxy_endpoint rename verification
# ─────────────────────────────────────────────────────────────────


class TestProxyEndpointRename:
    """Verify proxy_static was renamed to proxy_endpoint."""

    def test_proxy_endpoint_exists(self):
        """MeshtasticApiProxy should have proxy_endpoint method."""
        from gateway.meshtastic_api_proxy import MeshtasticApiProxy
        assert hasattr(MeshtasticApiProxy, 'proxy_endpoint')

    def test_proxy_static_removed(self):
        """MeshtasticApiProxy should NOT have proxy_static method."""
        from gateway.meshtastic_api_proxy import MeshtasticApiProxy
        assert not hasattr(MeshtasticApiProxy, 'proxy_static')
