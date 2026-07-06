"""Tests for the meshtastic-enabled gate inside ``prometheus_exporter``.

The follow-up to S6 (PR #110) addresses a deploy-time finding: even
when the operator sets ``MeshtasticConfig.enabled=False`` (PRs
#74/#75), Prometheus scrapes still blocked ~19s on a TCP connect to
meshtasticd:4403 because ``_collect_node_geojson`` instantiated
``MapDataCollector`` without the flag.

These tests verify:

1. ``_meshtastic_enabled()`` returns False when GatewayConfig disables
   it (the operator's explicit gateway-level intent).
2. ``_meshtastic_enabled()`` returns False when the deployment profile
   disables it (coarse profile-level switch — ``meshcore`` / ``monitor``).
3. ``_meshtastic_enabled()`` returns True only when BOTH say enabled
   (the safe default — emit metrics if config is unclear).
4. Config-load failures default to True so a misconfigured host
   doesn't suddenly stop emitting node metrics.
5. ``_collect_node_geojson`` passes the resolved flag through to
   ``MapDataCollector(meshtastic_enabled=...)`` — this is the line that
   actually fixes the latency.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from utils import prometheus_exporter as pe


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _mock_gateway_config(meshtastic_enabled: bool) -> MagicMock:
    cfg = MagicMock()
    cfg.meshtastic.enabled = meshtastic_enabled
    return cfg


def _mock_profile(meshtastic_enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(feature_flags={"meshtastic": meshtastic_enabled})


def _reset_node_cache():
    """Force the next ``_collect_node_geojson`` call to re-collect.

    Uses ``None`` (the cache sentinel introduced when the TTL was
    bumped) — setting to ``{}`` would falsely register as cached
    under the new ``is not None`` check.
    """
    pe._node_geojson_cache = None
    pe._node_geojson_cache_time = 0.0
    # The collector is now a process-wide singleton (built once, reused across
    # scrapes for perf) — reset it too so each test's mocked constructor is
    # re-invoked. (QA deferred low/perf, 2026-07-06.)
    pe._shared_collector = None


# ──────────────────────────────────────────────────────────────────────
# _meshtastic_enabled — config-source matrix
# ──────────────────────────────────────────────────────────────────────


class TestMeshtasticEnabledHelper:

    def test_both_enabled_returns_true(self):
        with patch.object(pe, "logger"), \
             patch("gateway.config.GatewayConfig.load",
                   return_value=_mock_gateway_config(True)), \
             patch("utils.deployment_profiles.load_or_detect_profile",
                   return_value=_mock_profile(True)):
            assert pe._meshtastic_enabled() is True

    def test_gateway_config_disabled_returns_false(self):
        """The meshanchor-server case: profile=full + gateway flag=false."""
        with patch("gateway.config.GatewayConfig.load",
                   return_value=_mock_gateway_config(False)), \
             patch("utils.deployment_profiles.load_or_detect_profile",
                   return_value=_mock_profile(True)):
            assert pe._meshtastic_enabled() is False

    def test_profile_disabled_returns_false(self):
        """The meshcore-profile case: profile flag=false."""
        with patch("gateway.config.GatewayConfig.load",
                   return_value=_mock_gateway_config(True)), \
             patch("utils.deployment_profiles.load_or_detect_profile",
                   return_value=_mock_profile(False)):
            assert pe._meshtastic_enabled() is False

    def test_both_disabled_returns_false(self):
        with patch("gateway.config.GatewayConfig.load",
                   return_value=_mock_gateway_config(False)), \
             patch("utils.deployment_profiles.load_or_detect_profile",
                   return_value=_mock_profile(False)):
            assert pe._meshtastic_enabled() is False

    def test_gateway_load_raises_falls_through_to_profile(self):
        """Profile-only check still applies when gateway config errors."""
        with patch("gateway.config.GatewayConfig.load",
                   side_effect=RuntimeError("config corrupt")), \
             patch("utils.deployment_profiles.load_or_detect_profile",
                   return_value=_mock_profile(False)):
            assert pe._meshtastic_enabled() is False

    def test_both_loaders_raise_defaults_true(self):
        """Misconfigured host keeps emitting — don't suddenly go silent."""
        with patch("gateway.config.GatewayConfig.load",
                   side_effect=RuntimeError("no config")), \
             patch("utils.deployment_profiles.load_or_detect_profile",
                   side_effect=RuntimeError("no profile")):
            assert pe._meshtastic_enabled() is True

    def test_missing_meshtastic_attr_defaults_true(self):
        """A GatewayConfig stub without the meshtastic field shouldn't
        explode — getattr defaults to True (the dataclass default)."""
        cfg = MagicMock()
        # Simulate an old config that doesn't have the meshtastic
        # block at all by removing the attribute lookup chain.
        cfg.meshtastic = SimpleNamespace()  # no .enabled
        with patch("gateway.config.GatewayConfig.load", return_value=cfg), \
             patch("utils.deployment_profiles.load_or_detect_profile",
                   return_value=_mock_profile(True)):
            assert pe._meshtastic_enabled() is True


# ──────────────────────────────────────────────────────────────────────
# _collect_node_geojson — flag plumbing
# ──────────────────────────────────────────────────────────────────────


class TestCollectNodeGeojsonPlumbing:
    """The actual perf fix: confirm the flag flows through to
    MapDataCollector. If this regresses, the ~19s scrape-latency bug
    on meshtastic-disabled hosts comes back."""

    def test_passes_disabled_flag_when_gateway_config_says_off(self):
        _reset_node_cache()
        fake_collector = MagicMock()
        fake_collector.collect.return_value = {"features": []}
        ctor = MagicMock(return_value=fake_collector)
        with patch.object(pe, "MapDataCollector", ctor), \
             patch.object(pe, "_HAS_MAP_COLLECTOR", True), \
             patch.object(pe, "_meshtastic_enabled", return_value=False):
            pe._collect_node_geojson()
        ctor.assert_called_once()
        # The constructor must receive meshtastic_enabled=False — that's
        # the kwarg MapDataCollector inspects (see map_data_collector.py
        # __init__ + line 456 guard).
        kwargs = ctor.call_args.kwargs
        assert kwargs.get("meshtastic_enabled") is False
        assert kwargs.get("enable_history") is False

    def test_passes_enabled_flag_when_both_configs_agree(self):
        _reset_node_cache()
        fake_collector = MagicMock()
        fake_collector.collect.return_value = {"features": []}
        ctor = MagicMock(return_value=fake_collector)
        with patch.object(pe, "MapDataCollector", ctor), \
             patch.object(pe, "_HAS_MAP_COLLECTOR", True), \
             patch.object(pe, "_meshtastic_enabled", return_value=True):
            pe._collect_node_geojson()
        kwargs = ctor.call_args.kwargs
        assert kwargs.get("meshtastic_enabled") is True

    def test_cache_short_circuit_skips_constructor_call(self):
        """The 5s cache must still apply — we don't want to load the
        config + build the collector on every back-to-back scrape."""
        _reset_node_cache()
        fake_collector = MagicMock()
        fake_collector.collect.return_value = {"features": ["seed"]}
        ctor = MagicMock(return_value=fake_collector)
        with patch.object(pe, "MapDataCollector", ctor), \
             patch.object(pe, "_HAS_MAP_COLLECTOR", True), \
             patch.object(pe, "_meshtastic_enabled", return_value=False):
            first = pe._collect_node_geojson()
            second = pe._collect_node_geojson()
        assert first == second
        # Second call returned from cache — constructor wasn't invoked
        # again. This is the existing _NODE_CACHE_TTL behavior; we're
        # asserting we didn't break it.
        assert ctor.call_count == 1

    def test_returns_empty_when_map_collector_unavailable(self):
        _reset_node_cache()
        with patch.object(pe, "_HAS_MAP_COLLECTOR", False):
            assert pe._collect_node_geojson() == {}


# ──────────────────────────────────────────────────────────────────────
# Cache semantics — TTL sizing + None sentinel
# ──────────────────────────────────────────────────────────────────────


class TestCacheSemantics:
    """The cache fix that landed alongside _meshtastic_enabled():
    longer TTL covers Prom scrape intervals, and the ``is not None``
    check correctly caches empty results so a transient empty response
    doesn't force every subsequent scrape to re-fetch."""

    def test_ttl_covers_typical_prom_scrape_intervals(self):
        """Prometheus default scrape interval is 15s; common values
        are 15-60s. A TTL <= scrape interval defeats the cache for
        every other scrape. 90s is generous enough that even slow
        scrape configs benefit, while bounding stale-data risk."""
        assert pe._NODE_CACHE_TTL >= 60.0
        assert pe._NODE_CACHE_TTL <= 300.0  # not absurdly stale either

    def test_empty_geojson_is_cached_not_re_fetched(self):
        """Regression guard: a previous version used
        ``if _node_geojson_cache:`` which falsy-checks empty dicts,
        so an empty ``{}`` from MapDataCollector forced every
        subsequent scrape to redo the expensive fetch."""
        _reset_node_cache()
        ctor = MagicMock(return_value=MagicMock(
            collect=MagicMock(return_value={}),
        ))
        with patch.object(pe, "MapDataCollector", ctor), \
             patch.object(pe, "_HAS_MAP_COLLECTOR", True), \
             patch.object(pe, "_meshtastic_enabled", return_value=False):
            first = pe._collect_node_geojson()
            second = pe._collect_node_geojson()
            third = pe._collect_node_geojson()
        # Empty dict is a legitimate cache value — only one fetch.
        assert ctor.call_count == 1
        assert first == {} and second == {} and third == {}

    def test_none_sentinel_initial_state_triggers_fetch(self):
        """The ``None`` sentinel — distinct from ``{}`` — is what
        signals "never fetched yet"."""
        _reset_node_cache()
        assert pe._node_geojson_cache is None
        ctor = MagicMock(return_value=MagicMock(
            collect=MagicMock(return_value={"features": [{"id": "x"}]}),
        ))
        with patch.object(pe, "MapDataCollector", ctor), \
             patch.object(pe, "_HAS_MAP_COLLECTOR", True), \
             patch.object(pe, "_meshtastic_enabled", return_value=False):
            pe._collect_node_geojson()
        assert ctor.call_count == 1
        # Now cache is populated and second call hits it.
        with patch.object(pe, "MapDataCollector", ctor), \
             patch.object(pe, "_HAS_MAP_COLLECTOR", True), \
             patch.object(pe, "_meshtastic_enabled", return_value=False):
            pe._collect_node_geojson()
        assert ctor.call_count == 1  # no extra constructor call

    def test_exception_path_returns_empty_on_first_failure(self):
        """If collect() raises before any cache entry exists, the
        function returns ``{}`` — without crashing or returning a
        falsy stale value that would be re-fetched."""
        _reset_node_cache()
        ctor = MagicMock(return_value=MagicMock(
            collect=MagicMock(side_effect=RuntimeError("bad day")),
        ))
        with patch.object(pe, "MapDataCollector", ctor), \
             patch.object(pe, "_HAS_MAP_COLLECTOR", True), \
             patch.object(pe, "_meshtastic_enabled", return_value=False):
            result = pe._collect_node_geojson()
        assert result == {}

    def test_exception_path_returns_stale_cache_when_available(self):
        """Once a cache entry exists, transient failures should NOT
        wipe it — fall back to the stale value."""
        _reset_node_cache()
        good = {"features": [{"id": "cached"}]}
        ctor = MagicMock(return_value=MagicMock(
            collect=MagicMock(return_value=good),
        ))
        with patch.object(pe, "MapDataCollector", ctor), \
             patch.object(pe, "_HAS_MAP_COLLECTOR", True), \
             patch.object(pe, "_meshtastic_enabled", return_value=False):
            pe._collect_node_geojson()
        # Force cache time well into the past so the next call
        # bypasses the freshness short-circuit and re-fetches.
        pe._node_geojson_cache_time = 0.0
        bad_ctor = MagicMock(return_value=MagicMock(
            collect=MagicMock(side_effect=RuntimeError("blew up")),
        ))
        with patch.object(pe, "MapDataCollector", bad_ctor), \
             patch.object(pe, "_HAS_MAP_COLLECTOR", True), \
             patch.object(pe, "_meshtastic_enabled", return_value=False):
            result = pe._collect_node_geojson()
        # Got the stale-but-good cache, not an empty dict.
        assert result == good
