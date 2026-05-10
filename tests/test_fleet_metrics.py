"""Tests for the bare ``/metrics`` Prometheus endpoint (S6).

This is wiring tests, not exporter tests — `prometheus_exporter` has
its own coverage. We assert:

1. Localhost gets 200 + Prometheus exposition format.
2. Non-localhost gets 403 (no scrape data leaks to the LAN).
3. The body parses as well-formed Prometheus exposition.
4. ``Content-Type`` matches the Prom 0.0.4 spec.
5. Gzip activates when the client advertises ``Accept-Encoding: gzip``
   *and* the payload is large enough for it to be worth the CPU.
"""

from __future__ import annotations

import gzip
import io
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from utils.map_http_handler import MapRequestHandler


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _make_handler(*, client_addr: str = "127.0.0.1",
                  accept_encoding: str = "") -> MapRequestHandler:
    """Build a stubbed MapRequestHandler bypassing __init__.

    Mirrors the pattern in ``test_map_http_age_filter.py``: hand-roll
    the attrs the request methods touch, leave the rest unset.
    """
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.path = "/metrics"
    h.headers = {"Accept-Encoding": accept_encoding} if accept_encoding else {}
    h.client_address = (client_addr, 50000)
    h.wfile = io.BytesIO()
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    h.send_error = MagicMock()
    return h


def _headers_dict(handler: MapRequestHandler) -> dict:
    """Flatten ``send_header`` calls into a plain dict (last-write-wins)."""
    out: dict = {}
    for call in handler.send_header.call_args_list:
        name, value = call.args
        out[name] = value
    return out


def _read_body(handler: MapRequestHandler, *, gzipped: bool) -> str:
    raw = handler.wfile.getvalue()
    if gzipped:
        raw = gzip.decompress(raw)
    return raw.decode("utf-8")


# A representative chunk of Prometheus exposition. ``PrometheusExporter``
# touches a lot of MeshAnchor internals (connection manager, RNS, MQTT,
# the message queue) — patching it out keeps these tests focused on the
# HTTP wiring.
_FAKE_BODY = (
    "# HELP meshanchor_node_count Total nodes seen\n"
    "# TYPE meshanchor_node_count gauge\n"
    'meshanchor_node_count{network="meshcore"} 42934\n'
    "# HELP meshanchor_service_up Service availability\n"
    "# TYPE meshanchor_service_up gauge\n"
    'meshanchor_service_up{name="rnsd"} 1\n'
    'meshanchor_service_up{name="meshtasticd"} 0\n'
)


# ──────────────────────────────────────────────────────────────────────
# Localhost gating
# ──────────────────────────────────────────────────────────────────────


class TestLocalhostGate:
    """The endpoint refuses to serve anything to non-loopback clients."""

    def test_localhost_gets_200(self):
        h = _make_handler(client_addr="127.0.0.1")
        with patch(
            "utils.prometheus_exporter.PrometheusExporter.export",
            return_value=_FAKE_BODY,
        ):
            h._serve_fleet_metrics()
        h.send_response.assert_called_once_with(200)
        h.send_error.assert_not_called()

    def test_ipv6_loopback_gets_200(self):
        h = _make_handler(client_addr="::1")
        with patch(
            "utils.prometheus_exporter.PrometheusExporter.export",
            return_value=_FAKE_BODY,
        ):
            h._serve_fleet_metrics()
        h.send_response.assert_called_once_with(200)

    def test_lan_client_gets_403(self):
        h = _make_handler(client_addr="192.168.1.42")
        h._serve_fleet_metrics()
        h.send_error.assert_called_once()
        status, _ = h.send_error.call_args.args
        assert status == 403
        h.send_response.assert_not_called()
        # No body whatsoever should have been written.
        assert h.wfile.getvalue() == b""

    def test_external_client_gets_403(self):
        h = _make_handler(client_addr="203.0.113.7")
        h._serve_fleet_metrics()
        h.send_error.assert_called_once()
        assert h.send_error.call_args.args[0] == 403


# ──────────────────────────────────────────────────────────────────────
# Body shape + headers
# ──────────────────────────────────────────────────────────────────────


class TestExpositionFormat:
    """The localhost response is a valid Prometheus exposition."""

    def test_content_type_header(self):
        h = _make_handler()
        with patch(
            "utils.prometheus_exporter.PrometheusExporter.export",
            return_value=_FAKE_BODY,
        ):
            h._serve_fleet_metrics()
        headers = _headers_dict(h)
        # The Prom 0.0.4 spec wants this exact form so scrapers can
        # tell text-format from OpenMetrics.
        assert headers["Content-Type"] == (
            "text/plain; version=0.0.4; charset=utf-8"
        )

    def test_no_cache_header(self):
        h = _make_handler()
        with patch(
            "utils.prometheus_exporter.PrometheusExporter.export",
            return_value=_FAKE_BODY,
        ):
            h._serve_fleet_metrics()
        assert _headers_dict(h)["Cache-Control"] == "no-cache"

    def test_body_parses_as_prometheus_exposition(self):
        """Each non-blank line is HELP, TYPE, or `name{labels} value`."""
        h = _make_handler()
        with patch(
            "utils.prometheus_exporter.PrometheusExporter.export",
            return_value=_FAKE_BODY,
        ):
            h._serve_fleet_metrics()
        body = _read_body(h, gzipped=False)
        for line in body.splitlines():
            if not line:
                continue
            if line.startswith("# HELP ") or line.startswith("# TYPE "):
                continue
            # Sample lines: `meshanchor_node_count{network="meshcore"} 42934`
            # or unlabeled: `meshanchor_node_count 42934`. Always ends
            # with a numeric value.
            parts = line.rsplit(" ", 1)
            assert len(parts) == 2, f"malformed line: {line!r}"
            float(parts[1])  # raises if not numeric


# ──────────────────────────────────────────────────────────────────────
# Gzip activation
# ──────────────────────────────────────────────────────────────────────


class TestGzip:
    """Compression on/off based on Accept-Encoding + body size."""

    def test_gzip_activates_when_supported_and_large(self):
        # Pad the body well above _GZIP_MIN_BYTES (1024) so compression
        # is worth the CPU. ``# HELP`` / ``# TYPE`` lines are ignored
        # by parsers so this stays a valid exposition.
        large_body = _FAKE_BODY + ("# pad " + "x" * 80 + "\n") * 50
        h = _make_handler(accept_encoding="gzip")
        # Patch out the map_metrics.render() append so this test
        # focuses purely on the gzip-on-large-body wiring. The
        # integration with map_metrics gets its own test below.
        with patch(
            "utils.prometheus_exporter.PrometheusExporter.export",
            return_value=large_body,
        ), patch("utils.map_metrics.render", return_value=(b"", "")):
            h._serve_fleet_metrics()
        headers = _headers_dict(h)
        assert headers.get("Content-Encoding") == "gzip"
        assert headers.get("Vary") == "Accept-Encoding"
        # The wire payload must round-trip through gzip and match.
        assert _read_body(h, gzipped=True) == large_body

    def test_no_gzip_when_client_doesnt_advertise(self):
        large_body = _FAKE_BODY + ("# pad " + "x" * 80 + "\n") * 50
        h = _make_handler(accept_encoding="")
        with patch(
            "utils.prometheus_exporter.PrometheusExporter.export",
            return_value=large_body,
        ), patch("utils.map_metrics.render", return_value=(b"", "")):
            h._serve_fleet_metrics()
        headers = _headers_dict(h)
        assert "Content-Encoding" not in headers
        assert _read_body(h, gzipped=False) == large_body

    def test_no_gzip_for_tiny_bodies(self):
        # Below _GZIP_MIN_BYTES — compression skipped even if advertised.
        tiny = "# tiny\nmeshanchor_up 1\n"
        h = _make_handler(accept_encoding="gzip")
        with patch(
            "utils.prometheus_exporter.PrometheusExporter.export",
            return_value=tiny,
        ):
            h._serve_fleet_metrics()
        assert "Content-Encoding" not in _headers_dict(h)


# ──────────────────────────────────────────────────────────────────────
# Failure handling
# ──────────────────────────────────────────────────────────────────────


class TestExporterFailure:
    """If PrometheusExporter explodes, return 500 instead of leaking
    a half-written body. Scrapers handle 5xx gracefully."""

    def test_exporter_raises_returns_500(self):
        h = _make_handler()
        with patch(
            "utils.prometheus_exporter.PrometheusExporter.export",
            side_effect=RuntimeError("collector blew up"),
        ):
            h._serve_fleet_metrics()
        h.send_error.assert_called_once()
        assert h.send_error.call_args.args[0] == 500
        h.send_response.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# /metrics integration with map_metrics (HTTP-side surface)
# ──────────────────────────────────────────────────────────────────────


class TestMetricsIntegration:
    """The 2026-05-09 reverse port from MeshForge appended an HTTP-side
    surface to /metrics. These tests guard the integration shape: the
    body now contains BOTH the hand-rolled exposition and the
    prometheus_client output, both valid Prom format."""

    def test_body_contains_both_surfaces(self):
        h = _make_handler()
        with patch(
            "utils.prometheus_exporter.PrometheusExporter.export",
            return_value=_FAKE_BODY,
        ):
            h._serve_fleet_metrics()
        body = _read_body(h, gzipped=False)
        # Primary surface (hand-rolled).
        assert "meshanchor_node_count" in body
        # Secondary surface (prometheus_client via map_metrics).
        # The metric exists at module import — even with no recorded
        # requests, the SERVICE_UP gauge is present.
        assert "meshanchor_map_service_up" in body

    def test_body_still_serves_when_map_metrics_returns_empty(self):
        """If prometheus_client isn't installed, render() returns
        empty bytes — primary surface must still serve."""
        h = _make_handler()
        with patch(
            "utils.prometheus_exporter.PrometheusExporter.export",
            return_value=_FAKE_BODY,
        ), patch("utils.map_metrics.render", return_value=(b"", "")):
            h._serve_fleet_metrics()
        h.send_response.assert_called_once_with(200)
        body = _read_body(h, gzipped=False)
        assert "meshanchor_node_count" in body


# ──────────────────────────────────────────────────────────────────────
# /healthz — lightweight up/down probe (reverse port from MeshForge)
# ──────────────────────────────────────────────────────────────────────


class TestHealthz:
    """/healthz must be cheap, unconditional 200, parseable JSON.
    Distinct from /metrics (heavyweight) — generic uptime probes
    target this surface."""

    def test_healthz_returns_200_ready(self):
        h = _make_handler()
        h.path = "/healthz"
        h._serve_healthz()
        h.send_response.assert_called_once_with(200)
        body = h.wfile.getvalue().decode()
        import json
        # _serve_json wraps in JSON; parse and assert state=ready.
        data = json.loads(body)
        assert data == {"state": "ready"}

    def test_healthz_route_dispatches_via_dispatch_get(self):
        """Ensure /healthz short-circuits to _serve_healthz from the
        normal _dispatch_get table — not via _serve_static_html or
        any other fall-through path."""
        h = _make_handler()
        h.path = "/healthz"
        # Stub the actual serve method to detect dispatch.
        h._serve_healthz = MagicMock()
        h._dispatch_get()
        h._serve_healthz.assert_called_once()

    def test_healthz_trailing_slash_also_dispatches(self):
        h = _make_handler()
        h.path = "/healthz/"
        h._serve_healthz = MagicMock()
        h._dispatch_get()
        h._serve_healthz.assert_called_once()


# ──────────────────────────────────────────────────────────────────────
# Endpoint label normalization (cardinality control)
# ──────────────────────────────────────────────────────────────────────


class TestEndpointLabel:
    """Bucket parametrized routes into stable templates so Prom
    cardinality stays bounded. One time-series per route, not one
    per node id."""

    def test_stable_routes_pass_through_unchanged(self):
        for path in ["/api/status", "/api/nodes/geojson", "/healthz",
                     "/metrics", "/fleet/slo"]:
            assert MapRequestHandler._endpoint_label(path) == path

    def test_empty_path_normalizes_to_root(self):
        assert MapRequestHandler._endpoint_label("") == "/"

    def test_trajectory_with_id_buckets_template(self):
        assert MapRequestHandler._endpoint_label(
            "/api/nodes/trajectory/!abc123"
        ) == "/api/nodes/trajectory/{id}"

    def test_coverage_with_coords_buckets_template(self):
        assert MapRequestHandler._endpoint_label(
            "/api/coverage/19.5/-155.5/30"
        ) == "/api/coverage/{lat}/{lon}/{alt}"

    def test_los_with_coords_buckets_template(self):
        assert MapRequestHandler._endpoint_label(
            "/api/los/19/-155/20/-156"
        ) == "/api/los/{lat1}/{lon1}/{lat2}/{lon2}"

    def test_meshtastic_proxy_buckets_to_wildcard(self):
        for path in ["/api/v1/fromradio", "/api/v1/toradio"]:
            assert MapRequestHandler._endpoint_label(path) == "/api/v1/*"

    def test_unknown_path_buckets_to_other(self):
        # Anything not in STABLE and not a known prefix bucket — keep
        # cardinality bounded by funneling into "/other".
        assert MapRequestHandler._endpoint_label("/random/path") == "/other"


# ──────────────────────────────────────────────────────────────────────
# do_GET HTTP instrumentation — captures method/endpoint/status/duration
# ──────────────────────────────────────────────────────────────────────


class TestHTTPInstrumentation:
    """The do_GET wrapper records every request to map_metrics.
    Failures in that recording must NEVER break dispatch — instrument
    the recording call as best-effort."""

    def test_send_response_captures_status_code(self):
        h = MapRequestHandler.__new__(MapRequestHandler)
        h.wfile = io.BytesIO()
        # Real send_response calls back to BaseHTTPRequestHandler which
        # writes a status line; simplest check: confirm _last_status
        # is set on the override.
        h.send_response = MapRequestHandler.send_response.__get__(h)
        # Bypass the parent call by patching it out — we only care
        # about the override's side-effect (storing _last_status).
        with patch(
            "http.server.SimpleHTTPRequestHandler.send_response"
        ):
            h.send_response(200, "OK")
        assert h._last_status == 200

    def test_do_get_records_http_metric_with_normalized_endpoint(self):
        h = _make_handler()
        h.path = "/api/nodes/trajectory/!abc123"
        h._dispatch_get = MagicMock(side_effect=lambda: None)
        with patch("utils.map_metrics.record_http") as rec:
            h.do_GET()
        rec.assert_called_once()
        kwargs = rec.call_args.kwargs
        assert kwargs["method"] == "GET"
        # Must be normalized — raw path with !abc123 would explode
        # cardinality.
        assert kwargs["endpoint"] == "/api/nodes/trajectory/{id}"
        assert kwargs["duration_s"] >= 0

    def test_metrics_recording_failure_doesnt_break_dispatch(self):
        """If record_http raises (e.g. prometheus_client not installed
        with a corner case), dispatch must complete normally."""
        h = _make_handler()
        h.path = "/api/status"
        h._dispatch_get = MagicMock()
        with patch("utils.map_metrics.record_http",
                   side_effect=RuntimeError("metrics broke")):
            h.do_GET()  # must not raise
        h._dispatch_get.assert_called_once()

    def test_do_get_records_5xx_when_handler_errors_with_500(self):
        """Status class is derived from _last_status. A 500 path must
        record as 5xx, not 0xx (the initial value)."""
        h = _make_handler()
        h.path = "/api/status"

        def _fake_dispatch():
            h._last_status = 500

        h._dispatch_get = _fake_dispatch
        with patch("utils.map_metrics.record_http") as rec:
            h.do_GET()
        rec.assert_called_once()
        assert rec.call_args.kwargs["status_code"] == 500


# ──────────────────────────────────────────────────────────────────────
# map_metrics module — helpers
# ──────────────────────────────────────────────────────────────────────


class TestMapMetricsHelpers:
    """Direct tests of the helper functions in utils.map_metrics."""

    def test_is_available_returns_bool(self):
        from utils import map_metrics
        assert isinstance(map_metrics.is_available(), bool)

    def test_render_returns_body_and_content_type(self):
        from utils import map_metrics
        body, ct = map_metrics.render()
        assert isinstance(body, bytes)
        assert ct.startswith("text/plain")

    def test_record_http_increments_counter(self):
        """Verify a delta on the counter sample value rather than
        absolute — the registry is module-shared so other tests
        inflate baselines."""
        from utils import map_metrics
        if not map_metrics.is_available():
            pytest.skip("prometheus_client not installed")
        labels = {
            "method": "GET",
            "endpoint": "/test_record_http_counter",
            "status_class": "2xx",
        }
        before = map_metrics.REGISTRY.get_sample_value(
            "meshanchor_map_http_requests_total", labels,
        ) or 0.0
        map_metrics.record_http(
            method="GET",
            endpoint="/test_record_http_counter",
            status_code=200,
            duration_s=0.05,
        )
        after = map_metrics.REGISTRY.get_sample_value(
            "meshanchor_map_http_requests_total", labels,
        )
        assert after == before + 1

    def test_record_http_status_class_is_bucketed_by_hundred(self):
        """200 → 2xx, 404 → 4xx, 503 → 5xx — keeps cardinality bounded."""
        from utils import map_metrics
        if not map_metrics.is_available():
            pytest.skip("prometheus_client not installed")
        for code, expected_class in [(200, "2xx"), (404, "4xx"),
                                     (503, "5xx"), (302, "3xx")]:
            map_metrics.record_http(
                method="GET",
                endpoint=f"/test_class_bucket_{expected_class}",
                status_code=code,
                duration_s=0.01,
            )
            sample = map_metrics.REGISTRY.get_sample_value(
                "meshanchor_map_http_requests_total",
                {
                    "method": "GET",
                    "endpoint": f"/test_class_bucket_{expected_class}",
                    "status_class": expected_class,
                },
            )
            assert sample == 1, f"code {code} should bucket to {expected_class}"
