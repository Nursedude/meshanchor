"""Tests for the ?max_age_days=N filter on /api/nodes/geojson.

The public meshcore.dev fetcher returns 40k+ historical nodes, many last
heard months ago. Without a serve-time age filter, the browser cluster
index build dominates page-load time. The filter:

- Drops features whose ``last_heard`` is older than N days
- Keeps features that have no ``last_heard`` (we can't prove they're stale)
- Always keeps ``is_local=True`` features (the operator's own radio)
- ``?max_age_days=0`` disables the filter entirely
- Default falls back to the operator's saved setting (ship default 30)
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from utils.map_http_handler import MapRequestHandler


def _feature(node_id: str, last_heard, is_local: bool = False, lat: float = 19.0, lon: float = -155.0) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "id": node_id,
            "name": node_id,
            "last_heard": last_heard,
            "is_local": is_local,
            "network": "meshcore",
        },
    }


def _make_handler(path: str, features: List[dict], settings_max_age_days: int = 30):
    """Build a stubbed MapRequestHandler instance bypassing __init__."""
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.path = path
    h.headers = {}  # _maybe_gzip needs an Accept-Encoding lookup
    h.client_address = ("127.0.0.1", 50000)
    h.wfile = io.BytesIO()
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()

    geojson = {"type": "FeatureCollection", "features": features, "properties": {}}
    h.collector = MagicMock()
    h.collector.collect.return_value = geojson
    settings = MagicMock()
    settings.get.return_value = settings_max_age_days
    h.collector._settings = settings
    return h


def _read_body(handler: MapRequestHandler) -> dict:
    """Decode the JSON body — handles gzip if present (no Accept-Encoding by default)."""
    raw = handler.wfile.getvalue()
    return json.loads(raw.decode("utf-8"))


# ── filter mechanics ────────────────────────────────────────────────────


class TestFilterByAge:
    def test_recent_features_kept(self):
        now = time.time()
        feats = [_feature("recent", now - 86400)]  # 1 day old
        kept = MapRequestHandler._filter_by_age(feats, max_age_days=30)
        assert len(kept) == 1

    def test_stale_features_dropped(self):
        now = time.time()
        feats = [_feature("stale", now - 60 * 86400)]  # 60 days old
        kept = MapRequestHandler._filter_by_age(feats, max_age_days=30)
        assert kept == []

    def test_features_without_last_heard_kept(self):
        feats = [_feature("missing", None)]
        kept = MapRequestHandler._filter_by_age(feats, max_age_days=30)
        assert len(kept) == 1

    def test_is_local_features_always_kept(self):
        """The operator's own radio must never be filtered out."""
        now = time.time()
        feats = [_feature("self", now - 365 * 86400, is_local=True)]
        kept = MapRequestHandler._filter_by_age(feats, max_age_days=30)
        assert len(kept) == 1
        assert kept[0]["properties"]["is_local"] is True

    def test_mixed_set(self):
        now = time.time()
        feats = [
            _feature("fresh", now - 86400),
            _feature("stale", now - 60 * 86400),
            _feature("self", now - 365 * 86400, is_local=True),
            _feature("no_ts", None),
        ]
        kept = MapRequestHandler._filter_by_age(feats, max_age_days=30)
        ids = {f["properties"]["id"] for f in kept}
        assert ids == {"fresh", "self", "no_ts"}


# ── _resolve_max_age_days ────────────────────────────────────────────────


class TestResolveMaxAgeDays:
    def test_query_string_overrides_setting(self):
        h = _make_handler("/api/nodes/geojson?max_age_days=7", [], settings_max_age_days=30)
        assert h._resolve_max_age_days() == 7

    def test_query_string_zero_means_no_filter(self):
        h = _make_handler("/api/nodes/geojson?max_age_days=0", [], settings_max_age_days=30)
        assert h._resolve_max_age_days() == 0

    def test_negative_clamps_to_zero(self):
        h = _make_handler("/api/nodes/geojson?max_age_days=-5", [], settings_max_age_days=30)
        assert h._resolve_max_age_days() == 0

    def test_garbage_query_returns_none(self):
        """Bad input returns None — caller treats as 'no filter'."""
        h = _make_handler("/api/nodes/geojson?max_age_days=foo", [], settings_max_age_days=30)
        assert h._resolve_max_age_days() is None

    def test_falls_back_to_settings_when_no_query(self):
        h = _make_handler("/api/nodes/geojson", [], settings_max_age_days=14)
        assert h._resolve_max_age_days() == 14


# ── full /api/nodes/geojson serve path ────────────────────────────────────


class TestServeGeojsonAgeFilter:
    def test_default_filter_drops_stale_meshcore_dev_features(self):
        now = time.time()
        feats = [
            _feature("fresh", now - 5 * 86400),
            _feature("stale", now - 365 * 86400),
        ]
        h = _make_handler("/api/nodes/geojson", feats, settings_max_age_days=30)
        h._serve_geojson()
        body = _read_body(h)
        ids = {f["properties"]["id"] for f in body["features"]}
        assert ids == {"fresh"}
        assert body["properties"]["max_age_days"] == 30
        assert body["properties"]["features_after_age_filter"] == 1

    def test_max_age_zero_disables_filter(self):
        now = time.time()
        feats = [
            _feature("fresh", now - 5 * 86400),
            _feature("ancient", now - 1000 * 86400),
        ]
        h = _make_handler("/api/nodes/geojson?max_age_days=0", feats, settings_max_age_days=30)
        h._serve_geojson()
        body = _read_body(h)
        ids = {f["properties"]["id"] for f in body["features"]}
        assert ids == {"fresh", "ancient"}
        # Annotation skipped when filter is off.
        assert "max_age_days" not in body.get("properties", {})

    def test_no_collector_returns_empty_collection(self):
        h = MapRequestHandler.__new__(MapRequestHandler)
        h.path = "/api/nodes/geojson"
        h.headers = {}
        h.client_address = ("127.0.0.1", 50000)
        h.wfile = io.BytesIO()
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h.collector = None
        h._serve_geojson()
        body = _read_body(h)
        assert body == {"type": "FeatureCollection", "features": []}

    def test_collector_cache_not_mutated(self):
        """The serve path must never mutate the collector's cached dict.

        Map requests are concurrent via ThreadingHTTPServer; mutating
        the cache would race with the next request building from it.
        """
        now = time.time()
        feats = [_feature("fresh", now), _feature("stale", now - 90 * 86400)]
        cached = {"type": "FeatureCollection", "features": feats, "properties": {}}
        h = MapRequestHandler.__new__(MapRequestHandler)
        h.path = "/api/nodes/geojson"
        h.headers = {}
        h.client_address = ("127.0.0.1", 50000)
        h.wfile = io.BytesIO()
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h.collector = MagicMock()
        h.collector.collect.return_value = cached
        settings = MagicMock(); settings.get.return_value = 30
        h.collector._settings = settings

        h._serve_geojson()

        # Cached features list must still hold both originals.
        assert len(cached["features"]) == 2
