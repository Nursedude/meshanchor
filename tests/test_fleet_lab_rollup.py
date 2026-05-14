"""Tests for `_serve_fleet_lab_rollup` on MapRequestHandler.

The endpoint fetches MeshForge's `/lab/rollup` (and `/lab/synth-rollup`)
via HTTP from a configurable tester box and surfaces the markdown
bodies as JSON for the fleet.html "Federation Round-Trip" panel.

Patches `urllib.request.urlopen` directly so no real socket is opened.
Pattern mirrors `test_fleet_metrics.py`.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from utils.map_http_handler import MapRequestHandler  # noqa: E402


def _make_handler() -> MapRequestHandler:
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.path = "/fleet/lab-rollup"
    h.headers = {}
    h.client_address = ("127.0.0.1", 50000)
    h.wfile = io.BytesIO()
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    h.send_error = MagicMock()
    return h


def _decode_json(handler: MapRequestHandler) -> dict:
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def _fake_response(body_bytes: bytes):
    """Context-manager-shaped fake for urlopen()."""
    fake = MagicMock()
    fake.read.return_value = body_bytes
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    return fake


# ──────────────────────────────────────────────────────────────────────
# Happy path
# ──────────────────────────────────────────────────────────────────────


class TestSuccess:
    """Both endpoints reachable on the tester box."""

    def test_success_returns_both_rollups(self):
        # urlopen is called twice — once for /lab/rollup, once for
        # /lab/synth-rollup. Return different bodies so we can assert
        # they land in the right fields.
        responses = [
            _fake_response(b"## Lab traffic rollup\n\n| pair | samples |\n"),
            _fake_response(b"## Synth soak rollup\n\n| pair | rtt |\n"),
        ]
        h = _make_handler()
        with patch.dict(os.environ, {"MESHANCHOR_LAB_ROLLUP_URL":
                                     "http://VolcanoAI:5000/lab/rollup"}), \
             patch("urllib.request.urlopen", side_effect=responses):
            h._serve_fleet_lab_rollup()

        h.send_response.assert_called_once_with(200)
        body = _decode_json(h)
        assert body["enabled"] is True
        assert body["source_url"] == "http://VolcanoAI:5000/lab/rollup"
        assert "Lab traffic rollup" in body["rollup_md"]
        assert "Synth soak rollup" in body["synth_md"]
        assert body["error"] is None


# ──────────────────────────────────────────────────────────────────────
# Disabled
# ──────────────────────────────────────────────────────────────────────


class TestDisabled:
    def test_empty_url_returns_disabled(self):
        h = _make_handler()
        with patch.dict(os.environ, {"MESHANCHOR_LAB_ROLLUP_URL": ""}):
            h._serve_fleet_lab_rollup()
        body = _decode_json(h)
        assert body["enabled"] is False
        assert body["rollup_md"] is None
        assert body["synth_md"] is None
        # error explains why
        assert "MESHANCHOR_LAB_ROLLUP_URL" in body["error"]


# ──────────────────────────────────────────────────────────────────────
# 404 fix-hint passthrough
# ──────────────────────────────────────────────────────────────────────


class TestFixHintPassthrough:
    """404 from upstream MEANS the MF side returned a fix-hint markdown
    body. We capture and surface it so the dashboard can render the
    actionable instructions instead of going silent."""

    def test_404_body_captured_for_rollup_md(self):
        hint_body = (b"# lab rollup not found\n\n"
                     b"Fix: install meshforge-lab-rollup.timer on this host.\n")
        err_rollup = urllib.error.HTTPError(
            url="http://VolcanoAI:5000/lab/rollup",
            code=404, msg="Not Found", hdrs=None,
            fp=io.BytesIO(hint_body),
        )
        # synth endpoint returns a different 404 hint
        synth_hint = b"# synth rollup not found\n\nNo synth_soak data dir.\n"
        err_synth = urllib.error.HTTPError(
            url="http://VolcanoAI:5000/lab/synth-rollup",
            code=404, msg="Not Found", hdrs=None,
            fp=io.BytesIO(synth_hint),
        )
        h = _make_handler()
        with patch.dict(os.environ, {"MESHANCHOR_LAB_ROLLUP_URL":
                                     "http://VolcanoAI:5000/lab/rollup"}), \
             patch("urllib.request.urlopen", side_effect=[err_rollup, err_synth]):
            h._serve_fleet_lab_rollup()
        body = _decode_json(h)
        # 404 doesn't bubble as `error`; the body itself is the value.
        assert body["error"] is None
        assert "lab rollup not found" in body["rollup_md"]
        assert "synth rollup not found" in body["synth_md"]
        # Endpoint still 200 — the data is what the panel needs.
        h.send_response.assert_called_once_with(200)


# ──────────────────────────────────────────────────────────────────────
# Connect / timeout / 5xx — surface in `error`
# ──────────────────────────────────────────────────────────────────────


class TestUpstreamFailure:
    def test_url_error_populates_error_field(self):
        h = _make_handler()
        with patch.dict(os.environ, {"MESHANCHOR_LAB_ROLLUP_URL":
                                     "http://VolcanoAI:5000/lab/rollup"}), \
             patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("connection refused")):
            h._serve_fleet_lab_rollup()
        body = _decode_json(h)
        # rollup_md fetch failed — error is populated. (synth path
        # doesn't repopulate `error` because it's secondary.)
        assert body["rollup_md"] is None
        assert body["error"] is not None
        assert "VolcanoAI" in body["error"]

    def test_http_500_populates_error_field(self):
        err = urllib.error.HTTPError(
            url="http://VolcanoAI:5000/lab/rollup",
            code=500, msg="Internal Server Error", hdrs=None,
            fp=io.BytesIO(b""),
        )
        # Second call (synth) also fails the same way
        err2 = urllib.error.HTTPError(
            url="http://VolcanoAI:5000/lab/synth-rollup",
            code=500, msg="Internal Server Error", hdrs=None,
            fp=io.BytesIO(b""),
        )
        h = _make_handler()
        with patch.dict(os.environ, {"MESHANCHOR_LAB_ROLLUP_URL":
                                     "http://VolcanoAI:5000/lab/rollup"}), \
             patch("urllib.request.urlopen", side_effect=[err, err2]):
            h._serve_fleet_lab_rollup()
        body = _decode_json(h)
        assert body["error"] is not None
        assert "500" in body["error"]


# ──────────────────────────────────────────────────────────────────────
# URL construction — /lab/rollup → /lab/synth-rollup
# ──────────────────────────────────────────────────────────────────────


class TestSynthURLDerivation:
    """The synth endpoint URL is derived by string-replacing
    '/lab/rollup' → '/lab/synth-rollup' on the configured base."""

    def test_synth_url_constructed_from_rollup_url(self):
        captured_urls = []

        def _capture(req, **_kw):
            # urllib.request.urlopen accepts either str or Request; we
            # always pass a Request object in the production code.
            url = req.full_url if hasattr(req, "full_url") else str(req)
            captured_urls.append(url)
            return _fake_response(b"")

        h = _make_handler()
        with patch.dict(os.environ, {"MESHANCHOR_LAB_ROLLUP_URL":
                                     "http://moc:5000/lab/rollup"}), \
             patch("urllib.request.urlopen", side_effect=_capture):
            h._serve_fleet_lab_rollup()

        assert captured_urls == [
            "http://moc:5000/lab/rollup",
            "http://moc:5000/lab/synth-rollup",
        ]
