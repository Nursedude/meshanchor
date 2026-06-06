"""
Map HTTP Handler - HTTP request handling for MeshAnchor Map Server.

Provides the HTTP endpoint logic for the live map and APIs.
This module is used by MapServer in map_data_service.py.

Endpoints:
- GET /              -> node_map.html (the live map)
- GET /api/nodes/geojson  -> live node GeoJSON from all sources
- GET /api/nodes/history  -> node history stats + unique nodes (24h)
- GET /api/nodes/directory -> persistent node directory (Issue #49) — every
                              cached node across protocols, including those
                              older than the observations retention window
- GET /api/nodes/trajectory/<id> -> trajectory GeoJSON for a node
- GET /api/nodes/snapshot -> historical network snapshot for playback
- GET /api/messages/queue -> pending OUTBOUND messages from gateway queue
- GET /api/messages/received -> RECEIVED inbound messages from mesh
- GET /api/messages/rx-status -> MessageListener status (RX enabled?)
- GET /api/network/topology -> network topology for D3.js visualization
- GET /api/status    -> server health check + history stats
- GET /*             -> static files from web/

Meshtastic API Proxy (MeshAnchor-owned):
- GET  /api/v1/fromradio -> multiplexed protobuf packets from meshtasticd
- PUT  /api/v1/toradio   -> forwarded to meshtasticd
- GET  /json/nodes       -> proxied + sanitized from meshtasticd
- GET  /json/report      -> proxied from meshtasticd

Meshtastic Web Client (MeshAnchor-owned, served from disk):
- GET  /mesh/            -> meshtastic web client (from /usr/share/meshtasticd/web/)
- GET  /mesh/api/v1/*    -> routed through MeshAnchor multiplexed proxy
- GET  /mesh/json/*      -> routed through MeshAnchor sanitized proxy

Radio Control API (MeshAnchor-owned):
- GET /api/radio/info     -> radio device information
- GET /api/radio/nodes    -> nodes from connected radio
- GET /api/radio/channels -> channels from connected radio
- GET /api/radio/status   -> radio connection status
- POST /api/radio/message -> send message via radio
"""

import gzip
import json
import ipaddress
import logging
import mimetypes
import os
import re
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Optional dependency imports via safe_import ──────────────────────
from utils.safe_import import safe_import

_get_connection_manager, _ConnectionMode, _HAS_MESHTASTIC_CONN = safe_import(
    'utils.meshtastic_connection', 'get_connection_manager', 'ConnectionMode'
)
_SRTMProvider, _LOSAnalyzer, _HAS_TERRAIN = safe_import(
    'utils.terrain', 'SRTMProvider', 'LOSAnalyzer'
)
# MF Issue #74 port: the class is PersistentMessageQueue — the old
# 'MessageQueue' name never existed, so _HAS_MSG_QUEUE was always
# False and the /api/messages/queue SQLite branch was dead code
# (silently served the cache-file fallback).
_MessageQueue, _HAS_MSG_QUEUE = safe_import(
    'gateway.message_queue', 'PersistentMessageQueue'
)
from commands import messaging
_get_listener_status, _HAS_MSG_LISTENER = safe_import(
    'utils.message_listener', 'get_listener_status'
)
_get_websocket_server, _is_websocket_available, _HAS_WS_SERVER = safe_import(
    'utils.websocket_server', 'get_websocket_server', 'is_websocket_available'
)

# Ensure modern web asset MIME types are recognized (Python may lack these)
mimetypes.add_type('application/javascript', '.mjs')
mimetypes.add_type('font/woff2', '.woff2')
mimetypes.add_type('application/wasm', '.wasm')
mimetypes.add_type('image/webp', '.webp')
mimetypes.add_type('image/avif', '.avif')


from utils._map_meshtastic_proxy import MeshtasticProxyMixin
from utils._map_radio_endpoints import RadioEndpointsMixin
from utils._map_fleet import FleetEndpointsMixin


class MapRequestHandler(FleetEndpointsMixin, RadioEndpointsMixin, MeshtasticProxyMixin, SimpleHTTPRequestHandler):
    """HTTP handler that serves the map HTML and node GeoJSON API."""

    collector = None  # MapDataCollector instance
    web_dir: Optional[str] = None
    # CORS: None = allow all, list = allow specific origins
    allowed_origins: Optional[List[str]] = None
    # Meshtastic API proxy (deprecated — always None, kept for graceful 503 responses)
    api_proxy = None

    # Default allowed origins when none explicitly configured
    _DEFAULT_ORIGINS = ['http://localhost', 'https://localhost']

    def _is_localhost(self) -> bool:
        """Check if the request originates from localhost.

        All mutating endpoints (radio TX, device restart, toradio proxy)
        MUST gate on this to prevent LAN/mesh clients from controlling
        the radio or device. Handles IPv4, IPv6, and mapped addresses.
        """
        try:
            client_ip = ipaddress.ip_address(self.client_address[0])
            return client_ip.is_loopback
        except ValueError:
            return False

    def _send_cors_header(self):
        """Send appropriate CORS header based on configuration.

        When allowed_origins is None: restrict to localhost (secure default)
        When allowed_origins is a list: only allow those origins

        If the request origin is not permitted, no Access-Control-Allow-Origin
        header is sent at all. The previous fallback to
        ``http://localhost:5000`` for any unknown origin was actively
        misleading — the browser saw a mismatch between Origin and
        Allow-Origin and rejected the response, but the rejection mode
        was harder to diagnose than just dropping the header outright
        (which produces the same outcome but doesn't leak an entry from
        the allowlist regardless of who asked).

        Mirrors MF's _send_cors_header in
        meshforge/src/utils/map_http_handler.py.
        """
        origin = self.headers.get('Origin', '')
        if not origin:
            return
        origins = self.allowed_origins if self.allowed_origins is not None else self._DEFAULT_ORIGINS
        if any(origin.startswith(allowed) for allowed in origins):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')

    def send_response(self, code, message=None):
        """Override to capture status code for HTTP-side instrumentation.

        ``BaseHTTPRequestHandler.send_response`` writes the status
        line but doesn't expose the code. The do_GET wrapper below
        needs it to bucket requests as 2xx/3xx/4xx/5xx in Prometheus.
        Storing on ``self`` is per-request safe — each request is a
        fresh handler instance under ThreadingHTTPServer.
        """
        self._last_status = code
        super().send_response(code, message)

    @staticmethod
    def _endpoint_label(path_only: str) -> str:
        """Normalize a request path into a stable Prometheus label.

        Bucket parametrized paths (e.g. ``/api/nodes/trajectory/<id>``)
        into a single template so cardinality stays bounded — one
        time-series per route, not one per node id. This is the
        bag-of-routes downstream Grafana panels will graph.
        """
        # Stable, no-parameter routes → return as-is.
        STABLE = (
            "", "/index.html", "/healthz", "/metrics",
            "/api/status", "/api/nodes/geojson", "/api/nodes/history",
            "/api/nodes/directory",
            "/api/messages/queue", "/api/messages/rx-status",
            "/api/messages/received",
            "/api/gateway/queue", "/api/gateway/delivery",
            "/api/network/topology", "/api/weather",
            "/api/websocket/status", "/api/proxy/status",
            "/api/radio/info", "/api/radio/nodes",
            "/api/radio/channels", "/api/radio/status",
            "/fleet", "/fleet.html",
            "/fleet/health", "/fleet/slo", "/fleet/activity",
            "/fleet/rollup", "/fleet/federation",
            "/fleet/history", "/fleet/blackouts",
            "/fleet/logs", "/fleet/tests", "/fleet/run-test",
            "/fleet/tracer-fires",
        )
        if path_only in STABLE:
            return path_only or "/"
        # Parametrized routes — bucket by prefix.
        if path_only.startswith("/api/nodes/trajectory/"):
            return "/api/nodes/trajectory/{id}"
        if path_only.startswith("/api/nodes/snapshot"):
            return "/api/nodes/snapshot"
        if path_only.startswith("/api/coverage/"):
            return "/api/coverage/{lat}/{lon}/{alt}"
        if path_only.startswith("/api/los/"):
            return "/api/los/{lat1}/{lon1}/{lat2}/{lon2}"
        if path_only.startswith("/api/v1/"):
            return "/api/v1/*"  # meshtastic proxy fan-out
        if path_only.startswith("/json/"):
            return "/json/*"  # meshtastic proxy fan-out
        if path_only.startswith("/mesh/"):
            return "/mesh/*"  # meshtastic web client
        return "/other"

    def _serve_healthz(self):
        """Lightweight up/down probe.

        Distinct from ``/metrics`` which is heavyweight (12.5MB
        meshcore.dev fetch on cold cache — see PR #112's 90s TTL).
        Generic is-host-up monitors (uptime checks, load balancer
        health probes) want a fast unconditional 200; this is that.

        Body carries ``state`` so a future warming pattern can be
        added without changing the response code. MA's map service
        binds and is ready immediately, so today the body is always
        ``{"state": "ready"}`` — but the field is here for parity
        with MeshForge's pattern when MA grows a warming concept.
        """
        self._serve_json({"state": "ready"}, status=200)

    def do_GET(self):
        # HTTP-side instrumentation wrapper. The inner dispatch logic
        # is unchanged — we only time + record around it. Failures in
        # the metrics layer are swallowed so they can never break a
        # response.
        import time as _time
        from urllib.parse import urlparse
        start = _time.perf_counter()
        try:
            path_only = urlparse(self.path).path.rstrip('/')
        except Exception:
            path_only = self.path or ""
        self._last_status = 0  # send_response will overwrite

        try:
            self._dispatch_get()
        finally:
            try:
                from utils import map_metrics
                duration = _time.perf_counter() - start
                map_metrics.record_http(
                    method="GET",
                    endpoint=self._endpoint_label(path_only),
                    status_code=self._last_status or 0,
                    duration_s=duration,
                )
            except Exception:
                pass  # metrics MUST NEVER break dispatch

    def _dispatch_get(self):
        # Lightweight /healthz first — never gated, never warming-blocked,
        # always cheap. Generic uptime probes hit this constantly so it
        # must short-circuit before any heavier work.
        if self.path == '/healthz' or self.path == '/healthz/':
            self._serve_healthz()
            return
        if self.path == '/api/nodes/geojson' or self.path == '/api/nodes/geojson/':
            self._serve_geojson()
        elif self.path == '/' or self.path == '/index.html':
            self._serve_map()
        elif self.path == '/api/status':
            self._serve_status()
        elif self.path == '/api/nodes/history':
            self._serve_history_stats()
        elif self.path == '/api/nodes/directory':
            self._serve_directory()
        elif self.path.startswith('/api/nodes/trajectory/'):
            node_id = self.path.split('/api/nodes/trajectory/', 1)[1].rstrip('/')
            self._serve_trajectory(node_id)
        elif self.path.startswith('/api/coverage/'):
            # Coverage prediction for a node: /api/coverage/<lat>/<lon>/<alt>
            from urllib.parse import urlparse
            path_only = urlparse(self.path).path
            parts = path_only.split('/api/coverage/', 1)[1].rstrip('/').split('/')
            self._serve_coverage(parts)
        elif self.path.startswith('/api/los/'):
            # Line of sight check: /api/los/<lat1>/<lon1>/<lat2>/<lon2>
            from urllib.parse import urlparse
            path_only = urlparse(self.path).path
            parts = path_only.split('/api/los/', 1)[1].rstrip('/').split('/')
            self._serve_los(parts)
        elif self.path.startswith('/api/nodes/snapshot'):
            # Historical snapshot: /api/nodes/snapshot?timestamp=<unix_ts>&window=300
            self._serve_snapshot()
        elif self.path == '/api/messages/queue' or self.path == '/api/messages/queue/':
            self._serve_message_queue()
        elif self.path.startswith('/api/messages/received'):
            self._serve_received_messages()
        elif self.path == '/api/messages/rx-status' or self.path == '/api/messages/rx-status/':
            self._serve_rx_status()
        elif self.path == '/api/gateway/queue' or self.path == '/api/gateway/queue/':
            self._serve_gateway_queue()
        elif self.path == '/api/gateway/delivery' or self.path == '/api/gateway/delivery/':
            self._serve_gateway_delivery()
        elif self.path == '/api/websocket/status' or self.path == '/api/websocket/status/':
            self._serve_websocket_status()
        elif self.path == '/api/network/topology' or self.path == '/api/network/topology/':
            self._serve_network_topology()
        elif self.path == '/api/weather' or self.path == '/api/weather/':
            self._serve_weather()
        # ─────────────────────────────────────────────────────────────
        # Meshtastic API Proxy - MeshAnchor owns the web client API
        # ─────────────────────────────────────────────────────────────
        elif self.path.startswith('/api/v1/fromradio'):
            self._proxy_fromradio()
        elif self.path == '/json/nodes' or self.path == '/json/nodes/':
            self._proxy_json('/json/nodes')
        elif self.path == '/json/report' or self.path == '/json/report/':
            self._proxy_json('/json/report')
        elif self.path == '/json/blink' or self.path == '/json/blink/':
            self._proxy_json('/json/blink')
        elif self.path.startswith('/mesh/') or self.path == '/mesh':
            self._serve_mesh_web_client()
        elif self.path == '/api/proxy/status' or self.path == '/api/proxy/status/':
            self._serve_proxy_status()
        # ─────────────────────────────────────────────────────────────
        # Radio Control API - MeshAnchor-owned radio access
        # ─────────────────────────────────────────────────────────────
        elif self.path == '/api/radio/info' or self.path == '/api/radio/info/':
            self._serve_radio_info()
        elif self.path == '/api/radio/nodes' or self.path == '/api/radio/nodes/':
            self._serve_radio_nodes()
        elif self.path == '/api/radio/channels' or self.path == '/api/radio/channels/':
            self._serve_radio_channels()
        elif self.path == '/api/radio/status' or self.path == '/api/radio/status/':
            self._serve_radio_status()
        # ─────────────────────────────────────────────────────────────
        # Fleet Monitor API — engineering-grade NOC dashboard surface
        # ─────────────────────────────────────────────────────────────
        elif self.path == '/fleet' or self.path == '/fleet/' or self.path == '/fleet.html':
            self._serve_fleet_dashboard()
        elif self.path == '/fleet/health' or self.path == '/fleet/health/':
            self._serve_fleet_health()
        elif self.path == '/fleet/slo' or self.path == '/fleet/slo/':
            self._serve_fleet_slo()
        elif self.path == '/fleet/activity' or self.path == '/fleet/activity/':
            self._serve_fleet_activity()
        elif self.path == '/fleet/rollup' or self.path == '/fleet/rollup/':
            self._serve_fleet_rollup()
        elif self.path == '/fleet/lab-rollup' or self.path == '/fleet/lab-rollup/':
            self._serve_fleet_lab_rollup()
        elif self.path == '/fleet/federation' or self.path == '/fleet/federation/':
            self._serve_fleet_federation()
        elif self.path == '/fleet/history' or self.path.startswith('/fleet/history?'):
            self._serve_fleet_history()
        elif self.path == '/fleet/blackouts' or self.path.startswith('/fleet/blackouts?') or self.path == '/fleet/blackouts/':
            self._serve_fleet_blackouts()
        elif self.path == '/fleet/logs' or self.path.startswith('/fleet/logs?'):
            self._serve_fleet_logs()
        elif self.path == '/fleet/tracer-fires' or self.path.startswith('/fleet/tracer-fires?'):
            self._serve_fleet_tracer_fires()
        elif self.path == '/fleet/tests' or self.path == '/fleet/tests/':
            self._serve_fleet_tests_list()
        # ─────────────────────────────────────────────────────────────
        # Prometheus exposition — bare `/metrics` per Prom convention.
        # Localhost-only; gates on _is_localhost() inside the handler.
        # ─────────────────────────────────────────────────────────────
        elif self.path == '/metrics' or self.path == '/metrics/':
            self._serve_fleet_metrics()
        else:
            # Serve static files from web/ directory
            if self.web_dir:
                self.directory = self.web_dir
            # For HTML files, serve with no-cache headers
            if self.path.endswith('.html'):
                self._serve_static_html()
            else:
                super().do_GET()

    def do_POST(self):
        """Handle POST requests for radio control, meshtastic API proxy,
        and the fleet test runner.

        Authorization model:
          - /fleet/run-test  → open to LAN; the request body's `test` id
            must match the `_FLEET_TESTS` allowlist (operator-triggered
            lab fires from the dashboard).
          - Everything else  → localhost-only (mutating radio operations).

        GET endpoints remain open for LAN/AREDN access.
        """
        # Fleet test runner is allowlist-protected — open to LAN so the
        # dashboard (which may be loaded from any /24 host) can fire the
        # safe set of lab units. Safety is enforced by `_FLEET_TESTS`
        # inside the handler, not by client IP.
        if self.path == '/fleet/run-test' or self.path == '/fleet/run-test/':
            self._serve_fleet_run_test()
            return

        if not self._is_localhost():
            self.send_error(403, "Radio control only allowed from localhost")
            return

        # ─────────────────────────────────────────────────────────────
        # Meshtastic API Proxy - POST endpoints
        # ─────────────────────────────────────────────────────────────
        if self.path.startswith('/api/v1/toradio'):
            self._proxy_toradio()
        elif self.path.startswith('/mesh/api/v1/toradio'):
            self._proxy_toradio()
        elif self.path in ('/json/blink', '/json/blink/', '/mesh/json/blink', '/mesh/json/blink/'):
            self._proxy_toradio_json('/json/blink')
        elif self.path in ('/restart', '/restart/', '/mesh/restart', '/mesh/restart/'):
            self._proxy_toradio_json('/restart')
        # ─────────────────────────────────────────────────────────────
        # Radio Control API - POST endpoints
        # ─────────────────────────────────────────────────────────────
        elif self.path == '/api/radio/message' or self.path == '/api/radio/message/':
            self._handle_send_message()
        else:
            self.send_error(404, "Not Found")

    def do_PUT(self):
        """Handle PUT requests (meshtastic web client uses PUT for toradio).

        All PUT endpoints are mutating (radio TX) — restricted to localhost.
        """
        if not self._is_localhost():
            self.send_error(403, "Radio control only allowed from localhost")
            return

        if self.path.startswith('/api/v1/toradio'):
            self._proxy_toradio()
        elif self.path.startswith('/mesh/api/v1/toradio'):
            self._proxy_toradio()
        else:
            self.send_error(404, "Not Found")

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self._send_cors_header()
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Accept')
        self.send_header('Content-Length', '0')
        self.end_headers()

    # Max body size for radio message POST (10 KB)
    _MAX_MESSAGE_BODY = 10240
    # Valid Meshtastic destination pattern: node IDs, channel prefixes, broadcast
    _VALID_DESTINATION = re.compile(r'^[!~^]?[a-zA-Z0-9]+$')

    def _handle_send_message(self):
        """Handle POST /api/radio/message - send a message via radio.

        Uses HTTP protobuf (send_text_direct) to avoid TCP contention
        with the meshtasticd web UI — fromradio is single-consumer.
        """
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0 or content_length > self._MAX_MESSAGE_BODY:
                self._serve_json({"error": "Invalid or oversized payload"}, status=400)
                return

            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body)

            text = data.get('text', '')
            destination = data.get('destination', '^all')

            if not text or len(text) > 500:
                self._serve_json({"error": "text is required (max 500 chars)"}, status=400)
                return

            if not self._VALID_DESTINATION.match(destination):
                self._serve_json({"error": "Invalid destination format"}, status=400)
                return

            # Convert string destination to int for send_text_direct
            dest_num = None  # None = broadcast (0xFFFFFFFF)
            if destination and destination != '^all':
                try:
                    if destination.startswith('!'):
                        dest_num = int(destination[1:], 16)
                    else:
                        dest_num = int(destination)
                except (ValueError, IndexError):
                    self._serve_json({"error": "Invalid destination format"}, status=400)
                    return

            # Prefer HTTP protobuf — no TCP contention with web UI
            try:
                from gateway.meshtastic_protobuf_client import send_text_direct
                success = send_text_direct(text=text, destination=dest_num)
                if success:
                    self._serve_json({
                        "success": True,
                        "message": "Sent via radio (delivery best-effort)",
                        "destination": destination,
                        "connection_mode": "http"
                    })
                    return
                else:
                    logger.debug("send_text_direct failed, trying TCP fallback")
            except ImportError:
                logger.debug("Protobuf client not available, trying TCP fallback")

            # Fallback: TCP connection manager
            conn = self._get_radio_connection()
            if not conn:
                self._serve_json({
                    "error": "Radio not available",
                    "detail": "meshtasticd not reachable via HTTP or TCP.",
                }, status=503)
                return

            success = conn.send_message(text, destination)
            if success:
                self._serve_json({
                    "success": True,
                    "message": "Sent via radio (delivery best-effort)",
                    "destination": destination,
                    "connection_mode": conn.get_mode()
                })
            else:
                self._serve_json({
                    "error": "Send failed",
                    "detail": "Verify meshtasticd is running.",
                }, status=502)

        except json.JSONDecodeError:
            self._serve_json({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.warning(f"Radio message send error: {e}")
            self._serve_json({"error": "Send failed"}, status=500)

    def _serve_static_html(self):
        """Serve static HTML files with no-cache headers."""
        from urllib.parse import urlparse, unquote
        path_only = unquote(urlparse(self.path).path).lstrip('/')

        if self.web_dir:
            file_path = Path(self.web_dir) / path_only
        else:
            file_path = Path(__file__).parent.parent.parent / "web" / path_only

        # Security: prevent path traversal
        try:
            base_dir = Path(self.web_dir) if self.web_dir else Path(__file__).parent.parent.parent / "web"
            file_path = file_path.resolve()
            base_dir = base_dir.resolve()
            if not str(file_path).startswith(str(base_dir)):
                self.send_error(403, "Forbidden")
                return
        except Exception:
            self.send_error(400, "Invalid path")
            return

        if file_path.exists() and file_path.is_file():
            with open(file_path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404, f"File not found: {path_only}")

    # Gzip threshold: payloads smaller than this skip compression so the
    # CPU spent on tiny responses doesn't outweigh the transfer savings.
    _GZIP_MIN_BYTES = 1024

    def _accepts_gzip(self) -> bool:
        """True if the client advertised gzip support."""
        ae = self.headers.get('Accept-Encoding', '')
        return 'gzip' in ae.lower()

    def _maybe_gzip(self, data: bytes) -> tuple[bytes, Optional[str]]:
        """Compress `data` with gzip if the client supports it and the
        payload is large enough to be worth it.

        Returns ``(payload_bytes, content_encoding)`` — caller writes the
        ``Content-Encoding`` header iff the second element is non-None.
        """
        if len(data) < self._GZIP_MIN_BYTES or not self._accepts_gzip():
            return data, None
        # compresslevel=6 = python default. For 28MB JSON of node features
        # this cuts the wire payload ~8.5x in well under 1s on a Pi 5.
        return gzip.compress(data, compresslevel=6), 'gzip'

    # Region bboxes: (lat_min, lat_max, lon_min, lon_max).
    # ``world`` (no filter) is handled by absence from this map. Pacific +
    # other dateline-crossing regions are intentionally omitted for now
    # because bbox math without longitude wrap-around mis-classifies them;
    # operator can fall back to ``world`` and zoom in.
    REGION_BBOXES = {
        # Continental US + Alaska + Hawaii + Puerto Rico/USVI
        "us": (15.0, 72.0, -170.0, -65.0),
        # North America (US + Canada + Mexico + Central America)
        "na": (7.0, 84.0, -170.0, -50.0),
        # Hawaiian Islands only
        "hi": (18.0, 23.0, -161.0, -154.0),
        # Europe (mainland + UK + Iceland + Scandinavia)
        "eu": (34.0, 72.0, -25.0, 45.0),
        # Asia (mainland; excludes east of dateline)
        "as": (-10.0, 60.0, 60.0, 145.0),
        # Australia + NZ
        "oc": (-50.0, -10.0, 110.0, 180.0),
    }

    def _serve_geojson(self):
        """Serve live node GeoJSON, with optional age and region filters.

        Query params:
          ``max_age_days=N``  Drop features whose ``last_heard`` is older
                              than N days. ``0`` disables the filter.
                              Default falls back to map_settings.max_age_days
                              (ship default 30).
          ``region=KEY``      Drop features outside the region bbox.
                              KEY in {us, na, hi, eu, as, oc, world}.
                              ``world`` disables the filter. Default falls
                              back to map_settings.region (ship default
                              ``us``).

        Both filters are critical for the public meshcore.dev fetcher,
        which can return 40k+ nodes — many old, many on other continents.
        Filtering at serve time keeps the collector cache warm; the operator
        can A/B with ``?max_age_days=0&region=world`` for the unfiltered
        set.
        """
        if not self.collector:
            self._serve_json({"type": "FeatureCollection", "features": []})
            return

        # Resolve filters first (cheap query/settings parse) — they form the
        # cache key, since they materially change the response.
        max_age_days = self._resolve_max_age_days()
        region = self._resolve_region()
        cache_key = (max_age_days, region)

        def _build_geojson():
            # Always shallow-copy before mutating — the collector cache is
            # shared across concurrent requests via ThreadingHTTPServer.
            geojson = self.collector.collect()
            features = geojson.get("features") or []

            annotations: Dict[str, Any] = {}
            if max_age_days is not None and max_age_days > 0:
                features = self._filter_by_age(features, max_age_days)
                annotations["max_age_days"] = max_age_days
            if region and region != "world":
                bbox = self.REGION_BBOXES.get(region)
                if bbox is not None:
                    features = self._filter_by_region(features, bbox)
                    annotations["region"] = region
                    annotations["region_bbox"] = list(bbox)

            if annotations:
                geojson = dict(geojson)
                geojson["features"] = features
                props = dict(geojson.get("properties") or {})
                props.update(annotations)
                props["features_after_filter"] = len(features)
                geojson["properties"] = props
            return geojson

        self._serve_cached(
            self.collector._geojson_response_cache, cache_key, _build_geojson,
        )

    def _resolve_max_age_days(self) -> Optional[int]:
        """Read max_age_days from the query string or settings, or None."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        raw = (qs.get("max_age_days") or [None])[0]
        if raw is not None:
            try:
                value = int(raw)
            except ValueError:
                return None
            return max(value, 0)
        # Fall back to operator default. SettingsManager lookups raise
        # nothing — missing keys return the default. 30 days is the
        # ship default; ``0`` means "never filter".
        try:
            settings = self.collector._settings
        except AttributeError:
            return 30
        if settings is None:
            return 30
        try:
            return int(settings.get("max_age_days", 30))
        except (TypeError, ValueError):
            return 30

    @staticmethod
    def _filter_by_age(features: list, max_age_days: int) -> list:
        """Keep features whose ``last_heard`` is within ``max_age_days``.

        Features without a numeric ``last_heard`` are kept (we can't prove
        they're stale). is_local features are always kept. The cutoff is
        wall-clock now − max_age_days.
        """
        cutoff = time.time() - (max_age_days * 86400)
        kept = []
        for f in features:
            props = f.get("properties") or {}
            if props.get("is_local"):
                kept.append(f)
                continue
            last_heard = props.get("last_heard")
            if not isinstance(last_heard, (int, float)):
                kept.append(f)
                continue
            if last_heard >= cutoff:
                kept.append(f)
        return kept

    def _resolve_region(self) -> Optional[str]:
        """Read region from the query string or settings, or None.

        Returns a known region key (``us``/``na``/``hi``/``eu``/``as``/
        ``oc``/``world``) or None when value is unrecognized. Caller
        treats None as "no filter".
        """
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        raw = (qs.get("region") or [None])[0]
        if raw is not None:
            value = raw.strip().lower()
            if value == "" or value == "world":
                return "world"
            if value in self.REGION_BBOXES:
                return value
            return None
        try:
            settings = self.collector._settings
        except AttributeError:
            return "us"
        if settings is None:
            return "us"
        try:
            saved = (settings.get("region", "us") or "us").strip().lower()
        except (TypeError, AttributeError):
            return "us"
        if saved == "world" or saved in self.REGION_BBOXES:
            return saved
        return "us"

    @staticmethod
    def _filter_by_region(features: list, bbox: tuple) -> list:
        """Keep features whose [lon, lat] falls inside ``bbox``.

        ``bbox`` is (lat_min, lat_max, lon_min, lon_max). is_local features
        are always kept (the NOC's own radio is in-region by definition,
        even if the bbox is wrong). Features without valid geometry are
        kept (the side-panel pipeline expects them).
        """
        lat_min, lat_max, lon_min, lon_max = bbox
        kept = []
        for f in features:
            props = f.get("properties") or {}
            if props.get("is_local"):
                kept.append(f)
                continue
            geom = f.get("geometry") or {}
            coords = geom.get("coordinates")
            if (
                not isinstance(coords, (list, tuple))
                or len(coords) < 2
            ):
                kept.append(f)
                continue
            try:
                lon, lat = float(coords[0]), float(coords[1])
            except (TypeError, ValueError):
                kept.append(f)
                continue
            if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
                kept.append(f)
        return kept

    def _serve_map(self):
        """Serve the node_map.html file."""
        if self.web_dir:
            map_path = Path(self.web_dir) / "node_map.html"
        else:
            map_path = Path(__file__).parent.parent.parent / "web" / "node_map.html"

        if map_path.exists():
            with open(map_path, 'rb') as f:
                data = f.read()
            payload, encoding = self._maybe_gzip(data)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            if encoding:
                self.send_header('Content-Encoding', encoding)
                self.send_header('Vary', 'Accept-Encoding')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_error(404, f"Map file not found: {map_path}")

    def _serve_status(self):
        """Serve server status including radio connection info."""
        status = {
            "status": "running",
            "time": datetime.now().isoformat(),
            "collector": self.collector is not None,
        }

        # Include history stats if available
        if self.collector and self.collector._history:
            try:
                status["history"] = self.collector._history.get_stats()
            except Exception:
                status["history"] = None

            # Directory stats (Issue #49) — persistent per-node cache
            # across protocols, with tiered retention. Surfaces total
            # count, by-network, by-source-origin, last-seen range so
            # operators can see at a glance how many MeshCore/AREDN/RNS
            # nodes are cached and which retention tier they fall into.
            try:
                status["directory"] = self.collector._history.get_directory_stats()
            except Exception as e:
                logger.debug(f"directory stats lookup failed: {e}")
                status["directory"] = None

        # Include radio connection status
        status["radio"] = self._get_radio_status_summary()

        # Response-cache observability (Issues #70/#71). Surfaces hit/miss/
        # coalesced counts per heavy endpoint so operators can confirm the
        # caches are coalescing work (and spot a regression where an endpoint
        # stops calling get_or_build, or a TTL too short to coalesce).
        if self.collector:
            for stat_key, attr in (
                ("geojson", "_geojson_response_cache"),
                ("topology", "_topology_response_cache"),
                ("directory_cache", "_directory_response_cache"),
            ):
                try:
                    cache = getattr(self.collector, attr, None)
                    if cache is not None:
                        status[stat_key] = {
                            "cache": {**cache.stats(), "ttl_s": cache.ttl_s}
                        }
                except Exception as e:
                    logger.debug(f"{stat_key} cache stats lookup failed: {e}")

        data = json.dumps(status).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self._send_cors_header()
        self.end_headers()
        self.wfile.write(data)

    def _get_radio_status_summary(self) -> Dict[str, Any]:
        """Get a summary of radio connection status for the status endpoint."""
        if not _HAS_MESHTASTIC_CONN:
            return {"available": False, "error": "meshtastic library not installed"}

        # Check TCP port (meshtasticd)
        tcp_available = False
        try:
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                tcp_available = sock.connect_ex(('localhost', 4403)) == 0
        except Exception as e:
            logger.debug(f"TCP port check failed: {e}")

        # Check USB serial device. Exclude any device claimed by MeshCore
        # via the persistent /dev/ttyMeshCore symlink (created by
        # scripts/99-meshcore.rules). Without this filter, on a MeshCore-
        # primary host the glob returns the RAK4631's /dev/ttyACM0 and we
        # report mode='serial' as if a Meshtastic radio were attached.
        import glob
        import os
        excluded: set = set()
        if os.path.exists('/dev/ttyMeshCore'):
            try:
                excluded.add(os.path.realpath('/dev/ttyMeshCore'))
            except OSError:
                pass
        usb_devices = [
            d for d in (glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*'))
            if os.path.realpath(d) not in excluded
        ]
        usb_available = len(usb_devices) > 0

        # Determine connection mode
        if tcp_available:
            mode = "tcp"
            connected = True
        elif usb_available:
            mode = "serial"
            connected = True
        else:
            mode = "none"
            connected = False

        return {
            "connected": connected,
            "mode": mode,
            "tcp_available": tcp_available,
            "usb_available": usb_available,
            "usb_devices": usb_devices if usb_available else [],
        }

    def _serve_history_stats(self):
        """Serve node history summary and unique nodes list."""
        if not self.collector or not self.collector._history:
            self._serve_json({"error": "history not available", "nodes": []})
            return

        history = self.collector._history
        result = {
            "stats": history.get_stats(),
            "nodes": history.get_unique_nodes(hours=24),
        }
        self._serve_json(result)

    def _serve_directory(self):
        """Serve the persistent node directory as a GeoJSON FeatureCollection.

        Returns every node ever heard (within tier retention) — superset
        of `/api/nodes/geojson`, which only covers what the latest
        collect cycle saw. Position-less nodes (MeshCore adverts without
        GPS, RNS announces) surface in the sibling `nodes_without_position`
        array, mirroring the convention from Issue #43.
        """
        if not self.collector or not self.collector._history:
            self._serve_json({
                "type": "FeatureCollection",
                "features": [],
                "properties": {"error": "history not available"},
                "nodes_without_position": [],
            })
            return

        def _build_directory():
            features, position_less = (
                self.collector._history.get_directory_snapshot(
                    include_position_less=True
                )
            )
            # Per-network breakdown alongside the full list — same shape
            # /api/status uses, so dashboards can consume either.
            by_network: Dict[str, int] = {}
            for entry in position_less:
                net = entry.get("network", "unknown")
                by_network[net] = by_network.get(net, 0) + 1
            return {
                "type": "FeatureCollection",
                "features": features,
                "properties": {
                    "generated_at": datetime.now().isoformat(),
                    "total_features": len(features),
                    "total_position_less": len(position_less),
                },
                "nodes_without_position": position_less,
                "nodes_without_position_by_network": by_network,
            }

        # No query params → single cache key. The snapshot exception path
        # serves a 500 uncached (errors must not be cached).
        try:
            self._serve_cached(
                self.collector._directory_response_cache, None, _build_directory,
            )
        except Exception as e:
            logger.error(f"directory snapshot failed: {e}")
            self._serve_json({
                "type": "FeatureCollection",
                "features": [],
                "properties": {"error": str(e)[:200]},
                "nodes_without_position": [],
            }, status=500)

    def _serve_trajectory(self, node_id: str):
        """Serve trajectory GeoJSON for a specific node."""
        if not self.collector or not self.collector._history:
            self._serve_json({"error": "history not available"})
            return

        # URL decode the node_id (! becomes %21 in URLs)
        from urllib.parse import unquote
        node_id = unquote(node_id)

        history = self.collector._history
        geojson = history.get_trajectory_geojson(node_id, hours=24)
        self._serve_json(geojson)

    def _serve_json(self, obj: Any, status: int = 200):
        """Helper to serve a JSON response (gzipped when client supports)."""
        data = json.dumps(obj).encode()
        payload, encoding = self._maybe_gzip(data)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        if encoding:
            self.send_header('Content-Encoding', encoding)
            self.send_header('Vary', 'Accept-Encoding')
        self.send_header('Content-Length', str(len(payload)))
        self._send_cors_header()
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(payload)

    def _serve_cached(self, cache, key, build_obj, status: int = 200):
        """Serve a JSON response through a ResponseByteCache.

        ``build_obj`` is a zero-arg callable returning the response dict.
        On a cache miss it is invoked and the result is json.dumps'd and
        (when worth it) gzipped ONCE; the (raw, gzip) bytes are cached for
        the cache's TTL. Concurrent callers for the same key coalesce onto
        one build instead of each repeating the multi-MB serialize+gzip
        under the GIL (MeshForge Issues #70/#71). Errors raised by
        ``build_obj`` propagate uncached — the caller serves them itself.
        """
        def _build():
            obj = build_obj()
            raw = json.dumps(obj).encode()
            gz = (
                gzip.compress(raw, compresslevel=6)
                if len(raw) >= self._GZIP_MIN_BYTES
                else None
            )
            return raw, gz

        raw_bytes, gzip_bytes, _was_built = cache.get_or_build(key, _build)

        if gzip_bytes is not None and self._accepts_gzip():
            payload, encoding = gzip_bytes, 'gzip'
        else:
            payload, encoding = raw_bytes, None

        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        if encoding:
            self.send_header('Content-Encoding', encoding)
            self.send_header('Vary', 'Accept-Encoding')
        self.send_header('Content-Length', str(len(payload)))
        self._send_cors_header()
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(payload)

    def _serve_coverage(self, parts: List[str]):
        """Serve terrain-aware coverage prediction for a location.

        URL: /api/coverage/<lat>/<lon>/<antenna_height_m>
        Optional query params: radius_km (default 10), freq_mhz (default 906)
        """
        try:
            if len(parts) < 3:
                self._serve_json({"error": "Usage: /api/coverage/<lat>/<lon>/<height_m>"})
                return

            lat = float(parts[0])
            lon = float(parts[1])
            alt = float(parts[2])

            # Parse query params
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            radius_km = float(params.get('radius_km', ['10'])[0])
            freq_mhz = float(params.get('freq_mhz', ['906'])[0])
            resolution = int(params.get('resolution', ['24'])[0])

            # Limit resolution for performance
            resolution = min(resolution, 48)
            radius_km = min(radius_km, 50)

            # Get coverage prediction from terrain analyzer
            if not _HAS_TERRAIN:
                self._serve_json({"error": "terrain module not available"})
                return
            try:
                provider = _SRTMProvider()
                analyzer = _LOSAnalyzer(provider)
                coverage = analyzer.coverage_grid(
                    lat, lon, alt,
                    radius_km=radius_km,
                    freq_mhz=freq_mhz,
                    resolution=resolution
                )
            except Exception as e:
                logger.error(f"Coverage calculation failed: {e}")
                self._serve_json({"error": f"calculation failed: {str(e)}"})
                return

            # Convert to GeoJSON for map display
            features = []
            for point in coverage:
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [point["lon"], point["lat"]]
                    },
                    "properties": {
                        "is_clear": point["is_clear"],
                        "total_loss_db": point["total_loss_db"],
                        "terrain_loss_db": point["terrain_loss_db"],
                        "fresnel_pct": point["fresnel_clearance_pct"],
                        "distance_m": point["distance_m"],
                        "bearing": point["bearing"],
                    }
                })

            result = {
                "type": "FeatureCollection",
                "features": features,
                "properties": {
                    "center": [lon, lat],
                    "antenna_height_m": alt,
                    "radius_km": radius_km,
                    "freq_mhz": freq_mhz,
                }
            }
            self._serve_json(result)

        except ValueError as e:
            self._serve_json({"error": f"Invalid parameters: {e}"})
        except Exception as e:
            logger.error(f"Coverage endpoint error: {e}")
            self._serve_json({"error": str(e)})

    def _serve_snapshot(self):
        """Serve a historical network snapshot for playback.

        URL: /api/nodes/snapshot?timestamp=<unix_ts>&window=300
        """
        from urllib.parse import parse_qs, urlparse

        try:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            timestamp = float(params.get('timestamp', [str(time.time())])[0])
            window = int(params.get('window', ['300'])[0])

            if not self.collector or not self.collector._history:
                self._serve_json({"error": "history not available", "features": []})
                return

            history = self.collector._history
            observations = history.get_snapshot(timestamp=timestamp, window_seconds=window)

            # Convert observations to GeoJSON features
            features = []
            for obs in observations:
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [obs.longitude, obs.latitude]
                    },
                    "properties": {
                        "id": obs.node_id,
                        "name": obs.name,
                        "network": obs.network,
                        "is_online": obs.is_online,
                        "snr": obs.snr,
                        "battery": obs.battery,
                        "hardware": obs.hardware,
                        "role": obs.role,
                        "via_mqtt": obs.via_mqtt,
                        "timestamp": obs.timestamp,
                    }
                })

            result = {
                "type": "FeatureCollection",
                "features": features,
                "properties": {
                    "snapshot_time": timestamp,
                    "window_seconds": window,
                    "node_count": len(features),
                }
            }
            self._serve_json(result)

        except ValueError as e:
            self._serve_json({"error": f"Invalid parameters: {e}"})
        except Exception as e:
            logger.error(f"Snapshot endpoint error: {e}")
            self._serve_json({"error": str(e)})

    def _serve_los(self, parts: List[str]):
        """Serve line-of-sight analysis between two points.

        URL: /api/los/<lat1>/<lon1>/<lat2>/<lon2>
        Optional query params: alt1, alt2 (antenna heights, default 10m), freq_mhz (default 906)
        """
        try:
            if len(parts) < 4:
                self._serve_json({"error": "Usage: /api/los/<lat1>/<lon1>/<lat2>/<lon2>"})
                return

            lat1 = float(parts[0])
            lon1 = float(parts[1])
            lat2 = float(parts[2])
            lon2 = float(parts[3])

            # Parse query params
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            alt1 = float(params.get('alt1', ['10'])[0])
            alt2 = float(params.get('alt2', ['10'])[0])
            freq_mhz = float(params.get('freq_mhz', ['906'])[0])

            # Calculate LOS
            if not _HAS_TERRAIN:
                self._serve_json({"error": "terrain module not available"})
                return
            try:
                provider = _SRTMProvider()
                analyzer = _LOSAnalyzer(provider)
                result = analyzer.analyze(lat1, lon1, alt1, lat2, lon2, alt2, freq_mhz)
            except Exception as e:
                logger.error(f"LOS calculation failed: {e}")
                self._serve_json({"error": f"calculation failed: {str(e)}"})
                return

            # Build elevation profile for visualization
            profile = []
            if hasattr(result, 'profile') and result.profile:
                for p in result.profile:
                    profile.append({
                        "distance_m": p.distance_m,
                        "elevation_m": p.ground_elevation,
                        "los_height_m": p.los_height,
                        "fresnel_top": p.los_height + p.fresnel_radius,
                        "fresnel_bottom": p.los_height - p.fresnel_radius,
                    })

            response = {
                "is_clear": result.is_clear,
                "distance_m": result.distance_m,
                "total_loss_db": result.total_loss_db,
                "terrain_loss_db": result.terrain_loss_db,
                "fresnel_clearance_pct": result.fresnel_clearance_pct,
                "obstruction_count": len(result.obstructions) if hasattr(result, 'obstructions') else 0,
                "profile": profile,
                "endpoints": {
                    "from": {"lat": lat1, "lon": lon1, "alt": alt1},
                    "to": {"lat": lat2, "lon": lon2, "alt": alt2},
                }
            }
            self._serve_json(response)

        except ValueError as e:
            self._serve_json({"error": f"Invalid parameters: {e}"})
        except Exception as e:
            logger.error(f"LOS endpoint error: {e}")
            self._serve_json({"error": str(e)})

    def _serve_gateway_queue(self):
        """Queue backpressure stats (MF Issue #74 probe port).

        Parity/federation surface — the in-process check_queue_backlog
        reads get_stats() directly; this endpoint serves the dashboard,
        operators, and MF-side federation the same shape MeshForge's
        /api/gateway/queue serves.
        """
        if not _HAS_MSG_QUEUE:
            self._serve_json(
                {"error": "message_queue_unavailable"}, status=503,
            )
            return
        try:
            stats = _MessageQueue().get_stats()
            stats["timestamp"] = time.time()
            self._serve_json(stats, status=200)
        except Exception as e:
            self._serve_json(
                {"error": "queue_stats_unavailable", "reason": str(e)},
                status=500,
            )

    def _serve_gateway_delivery(self):
        """Honest delivery lifecycle counters (MF Issue #74 probe port).

        Serves ``delivery_counters.snapshot()``: per-state totals
        (QUEUED/SENT/CONFIRMED/DROPPED), drop-reason histogram,
        per-protocol breakdown, confirmation_rate (None at zero
        traffic), recent-events ring, and the cross-process health
        block (preflight + write-error truth).
        """
        try:
            from gateway.delivery_counters import snapshot as _delivery_snap
            self._serve_json(_delivery_snap(), status=200)
        except Exception as e:
            self._serve_json(
                {"error": "delivery_counters_unavailable", "reason": str(e)},
                status=500,
            )

    def _serve_message_queue(self):
        """Serve pending messages from the gateway message queue."""
        messages = []

        # Try to load from SQLite message queue
        if not _HAS_MSG_QUEUE:
            logger.debug("MessageQueue not available")
        else:
            try:
                queue = _MessageQueue()
                pending = queue.get_pending(limit=50)
                for msg in pending:
                    payload = msg.payload or {}
                    messages.append({
                        "id": msg.id,
                        "source": payload.get("source_id", ""),
                        "source_name": payload.get("source_name", ""),
                        "target": payload.get("destination_id", ""),
                        "target_name": payload.get("target_name", ""),
                        "network": msg.destination,
                        "status": msg.status.value,
                        "created_at": msg.created_at.isoformat(),
                        "message_type": payload.get("message_type", "text"),
                    })
            except Exception as e:
                logger.debug(f"Message queue error: {e}")

        # Also check for cached queue file
        if not messages:
            try:
                queue_cache = self.collector._cache_dir / "message_queue.json" if self.collector else None
                if queue_cache and queue_cache.exists():
                    with open(queue_cache) as f:
                        data = json.load(f)
                    messages = data.get("messages", [])
            except Exception as e:
                logger.debug(f"Queue cache read failed: {e}")

        self._serve_json({
            "messages": messages,
            "count": len(messages),
            "timestamp": datetime.now().isoformat()
        })

    def _serve_received_messages(self):
        """Serve received (inbound) messages from the messages database.

        Query params:
            limit: Max messages to return (default 50)
            network: Filter by network (all, meshtastic, rns)
            since: Only messages after this ISO timestamp

        This endpoint returns messages RECEIVED from the mesh, stored by
        the MessageListener. Use /api/messages/queue for pending OUTBOUND messages.
        """
        from urllib.parse import urlparse, parse_qs

        # Parse query parameters
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        limit = int(params.get('limit', ['50'])[0])
        network = params.get('network', ['all'])[0]
        since = params.get('since', [None])[0]

        messages = []

        try:
            result = messaging.get_messages(limit=limit, network=network)

            if result.success and result.data:
                all_messages = result.data.get('messages', [])

                # Filter by timestamp if 'since' is provided
                if since:
                    try:
                        since_dt = datetime.fromisoformat(since.replace('Z', '+00:00'))
                        all_messages = [
                            m for m in all_messages
                            if m.get('timestamp') and
                            datetime.fromisoformat(m['timestamp']) > since_dt
                        ]
                    except (ValueError, TypeError):
                        pass  # Invalid timestamp, skip filtering

                # Filter to show only received messages (from_id != 'local')
                messages = [m for m in all_messages if m.get('from_id') != 'local']

        except Exception as e:
            logger.debug(f"Error getting received messages: {e}")

        self._serve_json({
            "messages": messages,
            "count": len(messages),
            "timestamp": datetime.now().isoformat(),
            "endpoint": "received"  # Distinguish from /queue
        })

    def _serve_rx_status(self):
        """Serve the RX (message listener) status.

        Returns whether the MessageListener is running and stats
        about received messages.
        """
        status = {
            "state": "disconnected",
            "messages_received": 0,
            "last_message_time": None,
            "error": None,
        }

        if not _HAS_MSG_LISTENER:
            status["error"] = "MessageListener not available"
        else:
            try:
                status = _get_listener_status()
            except Exception as e:
                status["error"] = str(e)

        self._serve_json(status)

    def _serve_websocket_status(self):
        """Serve WebSocket server status and connection info.

        Returns WebSocket URL and stats for clients to connect.
        """
        status = {
            "available": False,
            "url": None,
            "port": 5001,
            "connected_clients": 0,
            "messages_broadcast": 0,
        }

        if not _HAS_WS_SERVER:
            status["error"] = "WebSocket server not available"
        else:
            try:
                if not _is_websocket_available():
                    status["error"] = "websockets library not installed"
                    self._serve_json(status)
                    return

                ws_server = _get_websocket_server()
                if ws_server._running:
                    stats = ws_server.stats
                    status["available"] = True
                    status["port"] = ws_server.port
                    # Build WebSocket URL based on request host
                    host = self.headers.get('Host', 'localhost:5000')
                    hostname = host.split(':')[0]
                    status["url"] = f"ws://{hostname}:{ws_server.port}/"
                    status["connected_clients"] = stats.connected_clients
                    status["messages_broadcast"] = stats.messages_broadcast
                    status["total_connections"] = stats.total_connections
                    if stats.started_at:
                        status["started_at"] = stats.started_at.isoformat()

            except Exception as e:
                status["error"] = str(e)

        self._serve_json(status)

    def _serve_network_topology(self):
        """Serve network topology data for D3.js visualization."""
        if not self.collector:
            self._serve_json({"error": "collector not available", "nodes": [], "links": []})
            return
        # No query params → single cache key. The O(n²) link build AND the
        # multi-MB serialize coalesce inside the response cache.
        self._serve_cached(
            self.collector._topology_response_cache, None, self._build_topology_obj,
        )

    def _build_topology_obj(self) -> Dict[str, Any]:
        """Build the network-topology dict (collect + O(n²) link build).

        Invoked by the topology response cache (ResponseByteCache) so the
        build and serialization are computed once per TTL and shared across
        concurrent callers.
        """
        geojson = self.collector.collect()
        nodes = []
        links = []
        node_map = {}
        aredn_links_added = set()  # Track AREDN links to avoid duplicates

        # Build nodes
        for feature in geojson.get("features", []):
            props = feature["properties"]
            coords = feature["geometry"]["coordinates"]
            node_id = props.get("id", f"{coords[0]}_{coords[1]}")

            network = "gateway" if props.get("is_gateway") else props.get("network", "meshtastic")

            node = {
                "id": node_id,
                "name": props.get("name", node_id),
                "network": network,
                "is_online": props.get("is_online", False),
                "is_gateway": props.get("is_gateway", False),
                "is_router": props.get("role") in ("ROUTER", "ROUTER_CLIENT", "REPEATER", "AREDN"),
                "lat": coords[1],
                "lon": coords[0],
                "snr": props.get("snr"),
                "battery": props.get("battery"),
                # AREDN-specific properties
                "link_type": props.get("link_type"),  # RF, DTD, TUN
                "link_quality": props.get("link_quality"),
            }
            nodes.append(node)
            node_map[node_id] = node

        # Build AREDN links from actual link data
        # AREDN neighbors have link_type property indicating real RF/DTD/TUN links
        aredn_nodes = [n for n in nodes if n["network"] == "aredn"]
        if aredn_nodes:
            # Find the local AREDN node (the one without link_type, it's the source)
            local_aredn = [n for n in aredn_nodes if not n.get("link_type")]
            neighbor_aredn = [n for n in aredn_nodes if n.get("link_type")]

            for local in local_aredn:
                for neighbor in neighbor_aredn:
                    # Create link from local to neighbor
                    link_key = tuple(sorted([local["id"], neighbor["id"]]))
                    if link_key not in aredn_links_added:
                        dist = self._haversine(local["lat"], local["lon"],
                                               neighbor["lat"], neighbor["lon"])
                        link_type_str = neighbor.get("link_type", "RF")
                        links.append({
                            "source": local["id"],
                            "target": neighbor["id"],
                            "type": f"aredn_{link_type_str.lower()}",  # aredn_rf, aredn_dtd, aredn_tun
                            "link_quality": neighbor.get("link_quality", 0),
                            "snr": neighbor.get("snr"),
                            "distance_km": round(dist, 2)
                        })
                        aredn_links_added.add(link_key)

        # Build links based on proximity and network relationships for non-AREDN nodes
        gateways = [n for n in nodes if (n["is_gateway"] or n["is_router"]) and n["network"] != "aredn"]
        regular_nodes = [n for n in nodes if not n["is_gateway"] and not n["is_router"] and n["network"] != "aredn"]

        # Connect regular nodes to nearest gateway/router
        for node in regular_nodes:
            if not node["is_online"]:
                continue

            nearest = None
            min_dist = float("inf")

            for gw in gateways:
                if not gw["is_online"]:
                    continue
                dist = self._haversine(node["lat"], node["lon"], gw["lat"], gw["lon"])
                if dist < min_dist and dist < 50:  # 50km max
                    min_dist = dist
                    nearest = gw

            if nearest:
                link_type = "gateway" if node["network"] != nearest["network"] else node["network"]
                links.append({
                    "source": node["id"],
                    "target": nearest["id"],
                    "type": link_type,
                    "distance_km": round(min_dist, 2)
                })

        # Connect gateways to each other
        for i, gw1 in enumerate(gateways):
            for gw2 in gateways[i+1:]:
                if not gw1["is_online"] or not gw2["is_online"]:
                    continue
                dist = self._haversine(gw1["lat"], gw1["lon"], gw2["lat"], gw2["lon"])
                if dist < 100:  # 100km for gateway-gateway
                    links.append({
                        "source": gw1["id"],
                        "target": gw2["id"],
                        "type": "gateway",
                        "distance_km": round(dist, 2)
                    })

        return {
            "nodes": nodes,
            "links": links,
            "network_counts": {
                "meshtastic": len([n for n in nodes if n["network"] == "meshtastic"]),
                "rns": len([n for n in nodes if n["network"] == "rns"]),
                "aredn": len([n for n in nodes if n["network"] == "aredn"]),
                "gateway": len([n for n in nodes if n["is_gateway"]])
            },
            "timestamp": datetime.now().isoformat()
        }

    # ─────────────────────────────────────────────────────────────────
    # Space Weather API
    # ─────────────────────────────────────────────────────────────────

    # Cache space weather data (refreshes every 15 minutes)
    _weather_cache: Optional[Dict] = None
    _weather_cache_time: float = 0
    _WEATHER_CACHE_TTL = 900  # 15 minutes

    def _serve_weather(self):
        """Serve space weather and HF band conditions for map overlay.

        Returns NOAA SWPC data: SFI, Kp, A-index, X-ray class,
        geomagnetic storm level, and per-band HF conditions.

        Cached for 15 minutes (space weather changes slowly).
        """
        now = time.time()

        # Return cached data if still fresh
        if (MapRequestHandler._weather_cache
                and (now - MapRequestHandler._weather_cache_time) < self._WEATHER_CACHE_TTL):
            self._serve_json(MapRequestHandler._weather_cache)
            return

        try:
            from commands.propagation import get_space_weather, get_band_conditions

            weather_result = get_space_weather()
            band_result = get_band_conditions()

            if weather_result.success:
                data = weather_result.data or {}
                # Merge band conditions if available
                if band_result.success and band_result.data:
                    data["band_conditions"] = band_result.data.get(
                        "bands", data.get("band_conditions", {})
                    )
                    data["overall_condition"] = band_result.data.get("overall", "Unknown")

                # Add mesh-relevant assessment
                kp = data.get("k_index")
                sfi = data.get("solar_flux")
                if kp is not None and kp >= 5:
                    data["mesh_impact"] = "degraded"
                    data["mesh_impact_note"] = (
                        f"Kp={kp} — Geomagnetic storm may cause "
                        "increased noise on LoRa frequencies"
                    )
                elif sfi and sfi >= 200:
                    data["mesh_impact"] = "elevated"
                    data["mesh_impact_note"] = (
                        f"SFI={int(sfi)} — High solar activity, "
                        "monitor for interference"
                    )
                else:
                    data["mesh_impact"] = "nominal"
                    data["mesh_impact_note"] = "Conditions favorable for mesh operations"

                data["cached_at"] = now

                # Cache the result
                MapRequestHandler._weather_cache = data
                MapRequestHandler._weather_cache_time = now

                self._serve_json(data)
            else:
                self._serve_json({
                    "error": weather_result.error or "Space weather data unavailable",
                    "mesh_impact": "unknown",
                    "cached_at": now,
                })
        except Exception as e:
            logger.warning(f"Space weather fetch failed: {e}")
            self._serve_json({
                "error": str(e),
                "mesh_impact": "unknown",
                "cached_at": now,
            })

    # Meshtastic API proxy methods (_get_client_id, _proxy_fromradio,
    # _proxy_toradio, _proxy_json, _proxy_toradio_json) are inherited
    # from MeshtasticProxyMixin in _map_meshtastic_proxy.py

    # Mesh web client and radio API endpoints provided by RadioEndpointsMixin:
    # _serve_mesh_web_client, _rewrite_mesh_html, _serve_mesh_client_unavailable,
    # _serve_proxy_status, _get_radio_connection, _serve_radio_info,
    # _serve_radio_nodes, _serve_radio_channels, _serve_radio_status, _haversine

    def log_message(self, format, *args):
        """Route request logging through Python logger instead of stderr.

        The HTTP server runs in a background thread. Writing to
        stdout/stderr corrupts the whiptail/dialog TUI display,
        but errors still need to be visible in log files for debugging.
        """
        # Route through Python logger (goes to log file, not TUI)
        message = format % args if args else format
        if '40' in str(args) or '50' in str(args):
            # 4xx/5xx responses logged as warnings for debugging
            logger.warning("MapHTTP: %s", message)
        else:
            logger.debug("MapHTTP: %s", message)
