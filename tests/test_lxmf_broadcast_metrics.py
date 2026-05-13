"""Tests for utils.lxmf_broadcast_metrics — S4 of the reliability charter.

The module wraps prometheus_client. It must be importable + safely no-op
when prometheus_client isn't installed (Map service runs on hosts that
may lack the dep). When the dep IS available, the metric helpers must
register samples that show up in the /metrics exposition.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import lxmf_broadcast_metrics as m

# Skip the prometheus-required test cases when the dep isn't installed.
HAS_PROM = m.is_available()


class TestNoPromSafeguards:
    """Every helper must be safe to call when prometheus_client isn't there."""

    def test_render_returns_empty_when_no_prom(self):
        if HAS_PROM:
            pytest.skip("prometheus_client is installed in this env")
        body, content_type = m.render()
        assert body == b""
        assert "text/plain" in content_type

    def test_record_fanout_no_prom(self):
        if HAS_PROM:
            pytest.skip("prometheus_client is installed")
        # Must not raise
        m.record_fanout("local", "success", duration_s=0.05)
        m.record_fanout("external", "outbound_error")

    def test_record_state_transition_no_prom(self):
        if HAS_PROM:
            pytest.skip("prometheus_client is installed")
        m.record_state_transition("healthy", "degraded")

    def test_register_status_provider_no_prom(self):
        if HAS_PROM:
            pytest.skip("prometheus_client is installed")
        m.register_status_provider(lambda: None)
        m.unregister_status_provider()


@pytest.mark.skipif(not HAS_PROM, reason="prometheus_client not installed")
class TestPromRegistry:
    """When prometheus_client IS present, metrics must register + render."""

    def test_render_returns_nonempty(self):
        # Touching a counter establishes a series; render must contain it.
        m.record_fanout("local", "success", duration_s=0.01)
        body, _ = m.render()
        text = body.decode("utf-8")
        assert "meshanchor_lxmf_broadcast_fanouts_total" in text
        assert 'tier="local"' in text
        assert 'outcome="success"' in text

    def test_state_transition_metric(self):
        m.record_state_transition("healthy", "degraded")
        m.record_state_transition("degraded", "stale")
        body, _ = m.render()
        text = body.decode("utf-8")
        assert "meshanchor_lxmf_broadcast_state_transitions_total" in text
        assert 'from_state="healthy"' in text
        assert 'to_state="degraded"' in text

    def test_subscribers_collector_renders_tier_state_breakdown(self):
        """The Collector callback reads bridge status at scrape time and
        emits one gauge sample per (tier, state) combination present."""
        status = {
            "subscribers": [
                {"lxmf_hash": "aaaa", "tier": "local", "state": "healthy"},
                {"lxmf_hash": "bbbb", "tier": "local", "state": "degraded"},
                {"lxmf_hash": "cccc", "tier": "external", "state": "dead"},
                {"lxmf_hash": "dddd", "tier": "external", "state": "dead"},
            ]
        }
        m.register_status_provider(lambda: status)
        try:
            body, _ = m.render()
            text = body.decode("utf-8")
            assert "meshanchor_lxmf_broadcast_subscribers" in text
            # prometheus_client emits labels in alphabetical order, so
            # the rendered form is state="…",tier="…" — check via
            # combined substring matches rather than a positional one.
            assert 'state="healthy"' in text and 'tier="local"' in text
            assert 'state="degraded"' in text
            assert 'state="dead"' in text and 'tier="external"' in text
            # Dead samples: should aggregate to 2.0
            for line in text.splitlines():
                if (
                    line.startswith("meshanchor_lxmf_broadcast_subscribers{")
                    and 'state="dead"' in line
                    and 'tier="external"' in line
                ):
                    value = float(line.split()[-1])
                    assert value == 2.0
        finally:
            m.unregister_status_provider()

    def test_subscribers_collector_empty_when_provider_returns_none(self):
        m.register_status_provider(lambda: None)
        try:
            body, _ = m.render()
            text = body.decode("utf-8")
            # The family is registered (auto-describe) but no samples for it.
            assert all(
                "meshanchor_lxmf_broadcast_subscribers{" not in line
                for line in text.splitlines()
            )
        finally:
            m.unregister_status_provider()

    def test_subscribers_collector_swallows_provider_exception(self):
        def broken():
            raise RuntimeError("status read blew up")

        m.register_status_provider(broken)
        try:
            body, _ = m.render()
            # Render must NOT raise — the collector swallows.
            assert isinstance(body, bytes)
        finally:
            m.unregister_status_provider()

    def test_register_status_provider_replaces_existing(self):
        """Re-registering must swap, not stack — otherwise a daemon
        restart would leave dangling collectors and double-count."""
        m.register_status_provider(lambda: {"subscribers": [
            {"lxmf_hash": "a", "tier": "local", "state": "healthy"},
        ]})
        m.register_status_provider(lambda: {"subscribers": [
            {"lxmf_hash": "b", "tier": "local", "state": "healthy"},
            {"lxmf_hash": "c", "tier": "local", "state": "healthy"},
        ]})
        try:
            body, _ = m.render()
            text = body.decode("utf-8")
            for line in text.splitlines():
                if (
                    line.startswith("meshanchor_lxmf_broadcast_subscribers{")
                    and 'state="healthy"' in line
                    and 'tier="local"' in line
                ):
                    value = float(line.split()[-1])
                    assert value == 2.0
        finally:
            m.unregister_status_provider()

    def test_histogram_duration_observation(self):
        m.record_fanout("federation", "success", duration_s=0.5)
        body, _ = m.render()
        text = body.decode("utf-8")
        assert "meshanchor_lxmf_broadcast_fanout_duration_seconds" in text
        assert 'tier="federation"' in text
