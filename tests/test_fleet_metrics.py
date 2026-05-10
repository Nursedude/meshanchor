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
        with patch(
            "utils.prometheus_exporter.PrometheusExporter.export",
            return_value=large_body,
        ):
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
        ):
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
