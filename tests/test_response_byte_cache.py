"""Tests for the response-byte cache (ported from MeshForge Issues #70/#71).

The class is generic — MeshAnchor's geojson, topology, and directory
endpoints share the implementation, so the contracts pinned here cover
all three. TestHandlerCacheWiring proves the handler actually routes
through the cache (a second request inside the TTL skips the rebuild) and
that /api/status surfaces each cache's stats.
"""

from __future__ import annotations

import io
import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from utils._response_byte_cache import ResponseByteCache
from utils.map_http_handler import MapRequestHandler


class TestBasics:
    def test_constructor_rejects_nonpositive_ttl(self):
        with pytest.raises(ValueError):
            ResponseByteCache(ttl_s=0)
        with pytest.raises(ValueError):
            ResponseByteCache(ttl_s=-1.0)

    def test_get_returns_none_on_empty_cache(self):
        cache = ResponseByteCache(ttl_s=5.0)
        assert cache.get(None) is None
        assert cache.get("any_key") is None

    def test_get_or_build_returns_built_value_on_miss(self):
        cache = ResponseByteCache(ttl_s=5.0)
        raw, gz, was_built = cache.get_or_build(
            None, lambda: (b'{"k": 1}', b"gzbytes")
        )
        assert raw == b'{"k": 1}'
        assert gz == b"gzbytes"
        assert was_built is True

    def test_subsequent_get_returns_cached_bytes(self):
        cache = ResponseByteCache(ttl_s=5.0)
        cache.get_or_build(None, lambda: (b"raw1", b"gz1"))
        assert cache.get(None) == (b"raw1", b"gz1")

    def test_was_built_false_on_cache_hit(self):
        cache = ResponseByteCache(ttl_s=5.0)
        cache.get_or_build(None, lambda: (b"raw", b"gz"))
        build_calls = []
        _, _, was_built = cache.get_or_build(
            None, lambda: build_calls.append(1) or (b"unused", None)
        )
        assert was_built is False
        assert build_calls == [], "build_fn must NOT run on a hit"

    def test_build_exception_propagates_and_caches_nothing(self):
        cache = ResponseByteCache(ttl_s=5.0)

        def _boom():
            raise RuntimeError("build failed")

        with pytest.raises(RuntimeError):
            cache.get_or_build(None, _boom)
        # Nothing cached — a later good build still runs.
        raw, _, was_built = cache.get_or_build(None, lambda: (b"ok", None))
        assert (raw, was_built) == (b"ok", True)


class TestTTLExpiry:
    def test_entry_expires_after_ttl(self):
        cache = ResponseByteCache(ttl_s=0.05)
        cache.get_or_build(None, lambda: (b"v1", None))
        assert cache.get(None) == (b"v1", None)
        time.sleep(0.08)
        assert cache.get(None) is None, "entry must be invisible past TTL"

    def test_expired_entry_triggers_rebuild(self):
        cache = ResponseByteCache(ttl_s=0.05)
        build_calls: list = []

        def _build():
            build_calls.append(time.monotonic())
            return b"v" + str(len(build_calls)).encode(), None

        cache.get_or_build(None, _build)
        time.sleep(0.08)
        raw, _, was_built = cache.get_or_build(None, _build)
        assert was_built is True
        assert raw == b"v2"
        assert len(build_calls) == 2


class TestKeysIsolated:
    def test_different_keys_cache_independently(self):
        cache = ResponseByteCache(ttl_s=5.0)
        cache.get_or_build(None, lambda: (b"unfiltered", None))
        cache.get_or_build("live_rf", lambda: (b"filtered", None))
        assert cache.get(None) == (b"unfiltered", None)
        assert cache.get("live_rf") == (b"filtered", None)

    def test_one_key_expiry_does_not_affect_another(self):
        cache = ResponseByteCache(ttl_s=0.05)
        cache.get_or_build(None, lambda: (b"a", None))
        time.sleep(0.02)
        cache.get_or_build("k2", lambda: (b"b", None))
        time.sleep(0.04)
        # None has expired (>0.05s old) but k2 is still fresh (~0.04s).
        assert cache.get(None) is None
        assert cache.get("k2") == (b"b", None)


class TestTupleKeys:
    """Geojson keys on a ``(max_age_days, region)`` tuple."""

    def test_tuple_key_round_trips(self):
        cache = ResponseByteCache(ttl_s=5.0)
        key = (30, "us")
        cache.get_or_build(key, lambda: (b"a", None))
        assert cache.get(key) == (b"a", None)

    def test_distinct_tuple_keys_cache_independently(self):
        cache = ResponseByteCache(ttl_s=5.0)
        k_a = (30, "us")
        k_b = (0, "world")
        cache.get_or_build(k_a, lambda: (b"filtered", None))
        cache.get_or_build(k_b, lambda: (b"unfiltered", None))
        assert cache.get(k_a) == (b"filtered", None)
        assert cache.get(k_b) == (b"unfiltered", None)


class TestSingleFlightCoalescing:
    """Headline regression guard: without single-flight a burst of
    concurrent requests each rebuilds and stacks behind the GIL."""

    def test_concurrent_misses_invoke_build_fn_once(self):
        cache = ResponseByteCache(ttl_s=5.0)
        call_counter = {"n": 0}
        lock = threading.Lock()

        def _slow_build():
            with lock:
                call_counter["n"] += 1
            time.sleep(0.05)   # simulate slow json.dumps + gzip
            return b"shared", b"shared_gz"

        results: list = []
        ready = threading.Barrier(8)

        def _worker():
            ready.wait()
            results.append(cache.get_or_build(None, _slow_build))

        threads = [threading.Thread(target=_worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert call_counter["n"] == 1, (
            f"single-flight broken — build_fn ran {call_counter['n']}x "
            f"for 8 concurrent misses."
        )
        assert len(results) == 8
        assert sum(1 for r in results if r[2]) == 1
        assert all(r[0] == b"shared" and r[1] == b"shared_gz" for r in results)

    def test_coalesced_count_tracks_waiters(self):
        cache = ResponseByteCache(ttl_s=5.0)
        ready = threading.Barrier(4)

        def _slow():
            time.sleep(0.03)
            return b"x", None

        def _worker():
            ready.wait()
            cache.get_or_build(None, _slow)

        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        stats = cache.stats()
        assert stats["miss_count"] == 1
        assert stats["coalesced_count"] == 3


class TestObservabilityCounters:
    def test_hits_misses_and_entries_tracked(self):
        cache = ResponseByteCache(ttl_s=5.0)
        assert cache.stats() == {
            "hit_count": 0, "miss_count": 0,
            "coalesced_count": 0, "entry_count": 0,
        }
        cache.get_or_build(None, lambda: (b"a", None))       # miss
        cache.get_or_build(None, lambda: (b"unused", None))  # hit
        cache.get_or_build(None, lambda: (b"unused", None))  # hit
        cache.get_or_build("k2", lambda: (b"b", None))       # miss (new key)
        s = cache.stats()
        assert s["miss_count"] == 2
        assert s["hit_count"] == 2
        assert s["entry_count"] == 2


class TestMultiInstanceIsolation:
    """The collector owns THREE caches — pin they don't share state."""

    def test_two_caches_track_counters_independently(self):
        a = ResponseByteCache(ttl_s=5.0)
        b = ResponseByteCache(ttl_s=5.0)
        a.get_or_build(None, lambda: (b"a", None))           # miss on a
        a.get_or_build(None, lambda: (b"unused", None))      # hit on a
        b.get_or_build(None, lambda: (b"b", None))           # miss on b
        assert a.stats() == {
            "hit_count": 1, "miss_count": 1,
            "coalesced_count": 0, "entry_count": 1,
        }
        assert b.stats() == {
            "hit_count": 0, "miss_count": 1,
            "coalesced_count": 0, "entry_count": 1,
        }


def _make_geojson_handler(features):
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.path = "/api/nodes/geojson"
    h.headers = {}
    h.client_address = ("127.0.0.1", 50000)
    h.wfile = io.BytesIO()
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    h.collector = MagicMock()
    h.collector.collect.return_value = {
        "type": "FeatureCollection", "features": features, "properties": {},
    }
    settings = MagicMock()
    settings.get.side_effect = lambda key, default=None: {
        "max_age_days": 0, "region": "world",
    }.get(key, default)
    h.collector._settings = settings
    h.collector._geojson_response_cache = ResponseByteCache(ttl_s=5.0)
    return h


class TestHandlerCacheWiring:
    def test_second_geojson_request_skips_rebuild(self):
        """A second request inside the TTL must serve from cache — proving
        the endpoint routes through get_or_build (the whole point)."""
        h = _make_geojson_handler([])
        h._serve_geojson()
        h._serve_geojson()
        cache = h.collector._geojson_response_cache
        assert cache.stats()["miss_count"] == 1
        assert cache.stats()["hit_count"] == 1
        # collect() ran only on the miss, not the cache hit.
        assert h.collector.collect.call_count == 1

    def test_status_surfaces_cache_stats(self):
        """/api/status exposes the per-endpoint cache stats blocks."""
        h = MapRequestHandler.__new__(MapRequestHandler)
        h.path = "/api/status"
        h.headers = {}
        h.client_address = ("127.0.0.1", 50000)
        h.wfile = io.BytesIO()
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h.collector = MagicMock()
        h.collector._history = None
        h.collector._geojson_response_cache = ResponseByteCache(ttl_s=2.0)
        h.collector._topology_response_cache = ResponseByteCache(ttl_s=5.0)
        h.collector._directory_response_cache = ResponseByteCache(ttl_s=5.0)
        h._get_radio_status_summary = MagicMock(return_value={"connected": False})

        h._serve_status()
        body = json.loads(h.wfile.getvalue().decode())
        for k in ("geojson", "topology", "directory_cache"):
            assert k in body and "cache" in body[k]
            assert "hit_count" in body[k]["cache"]
            assert "ttl_s" in body[k]["cache"]
        assert body["geojson"]["cache"]["ttl_s"] == 2.0
