"""Tests for /fleet/* in-flight semaphore (MA Issue #128).

`utils._map_fleet.heavy_gate` decorator caps concurrent heavy fleet
handlers; over-cap requests return 429 + Retry-After: 2 immediately.
See Issue #131 for the operator-visible recurrence pattern this
addresses.
"""

from __future__ import annotations

import io
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from utils import _map_fleet  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Helpers — minimal fake handler + scoped semaphore override
# ──────────────────────────────────────────────────────────────────────


def _make_fake_handler():
    """Build the bare attrs heavy_gate touches: send_response, send_header,
    end_headers, wfile, _send_cors_header. Anything else (path, headers)
    is irrelevant — the wrapped method is mocked separately."""
    h = MagicMock()
    h.wfile = io.BytesIO()
    h._send_cors_header = MagicMock()
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    return h


def _headers_dict(handler) -> dict:
    out: dict = {}
    for call in handler.send_header.call_args_list:
        name, value = call.args
        out[name] = value
    return out


@pytest.fixture
def isolated_sem(monkeypatch):
    """Scope a fresh semaphore + counter per test so module-level state
    can't leak across cases."""
    sem = threading.BoundedSemaphore(2)  # small for easy exhaustion
    monkeypatch.setattr(_map_fleet, "_HEAVY_SEMAPHORE", sem)
    monkeypatch.setattr(_map_fleet, "_HEAVY_INFLIGHT_MAX", 2)
    yield sem


# ──────────────────────────────────────────────────────────────────────
# Env resolution
# ──────────────────────────────────────────────────────────────────────


class TestResolveInflightMax:
    """`_resolve_inflight_max` — defensive default + sane env handling."""

    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("MESHANCHOR_FLEET_HEAVY_INFLIGHT_MAX", raising=False)
        assert _map_fleet._resolve_inflight_max() == 16

    def test_env_integer_override(self, monkeypatch):
        monkeypatch.setenv("MESHANCHOR_FLEET_HEAVY_INFLIGHT_MAX", "12")
        assert _map_fleet._resolve_inflight_max() == 12

    def test_env_zero_disables_gate(self, monkeypatch):
        monkeypatch.setenv("MESHANCHOR_FLEET_HEAVY_INFLIGHT_MAX", "0")
        assert _map_fleet._resolve_inflight_max() == 0

    def test_env_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("MESHANCHOR_FLEET_HEAVY_INFLIGHT_MAX", "not-a-number")
        assert _map_fleet._resolve_inflight_max() == 16

    def test_env_negative_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("MESHANCHOR_FLEET_HEAVY_INFLIGHT_MAX", "-5")
        assert _map_fleet._resolve_inflight_max() == 16


# ──────────────────────────────────────────────────────────────────────
# Gating behavior
# ──────────────────────────────────────────────────────────────────────


class TestHeavyGate:
    """The `heavy_gate` decorator."""

    def test_under_cap_calls_wrapped_method(self, isolated_sem):
        wrapped = MagicMock(return_value="result")
        decorated = _map_fleet.heavy_gate(wrapped)
        h = _make_fake_handler()
        ret = decorated(h)
        wrapped.assert_called_once_with(h)
        assert ret == "result"
        # 429 path not taken
        h.send_response.assert_not_called()

    def test_at_cap_returns_429(self, isolated_sem):
        """When the semaphore is fully held, a new call gets 429 +
        Retry-After + small JSON body — wrapped method never runs."""
        # Drain the semaphore so the next acquire fails.
        isolated_sem.acquire(blocking=False)
        isolated_sem.acquire(blocking=False)

        wrapped = MagicMock()
        decorated = _map_fleet.heavy_gate(wrapped)
        h = _make_fake_handler()
        decorated(h)

        wrapped.assert_not_called()
        h.send_response.assert_called_once_with(429)
        headers = _headers_dict(h)
        assert headers.get("Retry-After") == "2"
        assert headers.get("Content-Type") == "application/json"
        body = h.wfile.getvalue().decode()
        assert "server_busy" in body
        assert "retry_after_s" in body
        assert "in_flight_max" in body

    def test_429_invokes_cors_header(self, isolated_sem):
        """Dashboard polls cross-origin in dev; 429 must still carry
        the CORS header the client expects, or the browser drops
        the response and we surface a misleading network error."""
        isolated_sem.acquire(blocking=False)
        isolated_sem.acquire(blocking=False)
        decorated = _map_fleet.heavy_gate(MagicMock())
        h = _make_fake_handler()
        decorated(h)
        h._send_cors_header.assert_called_once()

    def test_429_cors_failure_does_not_break_response(self, isolated_sem):
        """If _send_cors_header raises (older handler bases), the 429
        response must still be delivered — defense in depth."""
        isolated_sem.acquire(blocking=False)
        isolated_sem.acquire(blocking=False)
        decorated = _map_fleet.heavy_gate(MagicMock())
        h = _make_fake_handler()
        h._send_cors_header = MagicMock(side_effect=RuntimeError("boom"))
        decorated(h)  # must not propagate
        h.send_response.assert_called_once_with(429)

    def test_semaphore_released_after_success(self, isolated_sem):
        """One pass through should consume a slot then return it.
        Run cap+1 sequential calls; if release works, all succeed."""
        wrapped = MagicMock()
        decorated = _map_fleet.heavy_gate(wrapped)
        h = _make_fake_handler()
        for _ in range(5):
            decorated(h)
        assert wrapped.call_count == 5
        # Counter never bumped — no 429 emitted.
        h.send_response.assert_not_called()

    def test_semaphore_released_on_exception(self, isolated_sem):
        """A handler that raises must still release its slot — otherwise
        the semaphore drains monotonically and the gate degrades into
        permanent 429s."""
        boom_count = {"n": 0}

        def _boom(*_a, **_k):
            boom_count["n"] += 1
            raise RuntimeError("intentional")

        decorated = _map_fleet.heavy_gate(_boom)
        h = _make_fake_handler()
        # Cap is 2; run 5 calls. If release-on-exception is broken,
        # the 3rd would 429 instead of raising.
        for _ in range(5):
            with pytest.raises(RuntimeError):
                decorated(h)
        assert boom_count["n"] == 5
        h.send_response.assert_not_called()

    def test_disabled_gate_is_transparent(self, monkeypatch):
        """When cap is 0 / semaphore is None, decorator is a passthrough
        — no semaphore acquire, no 429, no metric bump."""
        monkeypatch.setattr(_map_fleet, "_HEAVY_SEMAPHORE", None)
        monkeypatch.setattr(_map_fleet, "_HEAVY_INFLIGHT_MAX", 0)
        wrapped = MagicMock(return_value="result")
        decorated = _map_fleet.heavy_gate(wrapped)
        h = _make_fake_handler()
        for _ in range(20):
            assert decorated(h) == "result"
        assert wrapped.call_count == 20
        h.send_response.assert_not_called()

    def test_concurrent_callers_respect_cap(self, isolated_sem):
        """With cap=2, three threads holding the slot simultaneously
        means one must get 429. Uses an Event to keep the first two
        threads in the wrapped method until the third tries."""
        in_flight = threading.Event()
        release = threading.Event()
        in_count = {"n": 0}
        in_lock = threading.Lock()

        def _slow(*_a, **_k):
            with in_lock:
                in_count["n"] += 1
                if in_count["n"] >= 2:
                    in_flight.set()
            release.wait(timeout=5)

        decorated = _map_fleet.heavy_gate(_slow)
        handlers = [_make_fake_handler() for _ in range(3)]

        threads = [
            threading.Thread(target=decorated, args=(handlers[i],))
            for i in range(2)
        ]
        for t in threads:
            t.start()
        assert in_flight.wait(timeout=5), "test wrapped never entered"

        # Third caller should hit a saturated semaphore -> 429.
        decorated(handlers[2])
        handlers[2].send_response.assert_called_once_with(429)
        headers = _headers_dict(handlers[2])
        assert headers.get("Retry-After") == "2"

        release.set()
        for t in threads:
            t.join(timeout=5)
        # First two went through cleanly.
        handlers[0].send_response.assert_not_called()
        handlers[1].send_response.assert_not_called()

    def test_busy_counter_increments_on_429(self, isolated_sem):
        """Each 429 should call into map_metrics.record_fleet_heavy_busy.
        Avoid importing prometheus_client at test time by patching the
        underlying record fn."""
        with patch("utils._map_fleet._record_busy") as rec:
            isolated_sem.acquire(blocking=False)
            isolated_sem.acquire(blocking=False)
            decorated = _map_fleet.heavy_gate(MagicMock())
            h = _make_fake_handler()
            decorated(h)
        rec.assert_called_once()

    def test_busy_counter_no_bump_on_success(self, isolated_sem):
        """When the handler completes normally the busy counter must
        stay flat — we only count load-shed events, not normal serves."""
        with patch("utils._map_fleet._record_busy") as rec:
            decorated = _map_fleet.heavy_gate(MagicMock())
            decorated(_make_fake_handler())
        rec.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# Integration with the live mixin
# ──────────────────────────────────────────────────────────────────────


class TestMixinIntegration:
    """Confirm the four heavy /fleet/* handlers are actually wrapped.

    Pure introspection — does not depend on importing the full handler
    base class, which pulls a large surface area."""

    def test_all_four_heavy_handlers_are_decorated(self):
        from utils._map_fleet import FleetEndpointsMixin
        # The decorator wraps via functools.wraps, so __wrapped__
        # is the original function. Presence of __wrapped__ confirms
        # decoration.
        for name in (
            "_serve_fleet_health",
            "_serve_fleet_slo",
            "_serve_fleet_activity",
            "_serve_fleet_rollup",
        ):
            method = getattr(FleetEndpointsMixin, name)
            assert hasattr(method, "__wrapped__"), (
                f"{name} must be wrapped by heavy_gate; without it the "
                "load-shedding gate is dead on arrival for that endpoint."
            )

    def test_non_heavy_handlers_not_decorated(self):
        """Light handlers (dashboard HTML, federation, logs, etc.)
        should NOT carry the gate — gating them would 429 useful
        click-into endpoints during a poll spike."""
        from utils._map_fleet import FleetEndpointsMixin
        for name in (
            "_serve_fleet_dashboard",
            "_serve_fleet_federation",
            "_serve_fleet_logs",
        ):
            method = getattr(FleetEndpointsMixin, name, None)
            if method is None:
                continue
            assert not hasattr(method, "__wrapped__"), (
                f"{name} should NOT be heavy_gated — only the four "
                "high-frequency dashboard polls go through the gate."
            )
