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
    """Force the next ``_collect_node_geojson`` call to re-collect."""
    pe._node_geojson_cache = {}
    pe._node_geojson_cache_time = 0.0


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
