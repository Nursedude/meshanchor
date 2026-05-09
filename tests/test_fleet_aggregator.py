"""Tests for monitoring.fleet_aggregator.

Covers Session 1's "always returns, never raises" contract: when any
source — service_check, boundary stats, daemon HTTP, collector — fails,
the snapshot still returns and the failure shows up in `errors`. Also
exercises the derived `slo_view` / `activity_view` shapes the dashboard
relies on.
"""
from __future__ import annotations

import json
import socket
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from monitoring import fleet_aggregator as fa


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_daemon_endpoints():
    """Patch the urllib fetch helper to canned daemon responses.

    Each test that wants daemon-side data passes a `responses` dict and
    yields control after asserting; the patcher restores on teardown.
    """
    def _make(responses):
        def fake(url, timeout=fa.DEFAULT_HTTP_TIMEOUT_S):
            for prefix, body in responses.items():
                if url.endswith(prefix):
                    if isinstance(body, Exception):
                        return None, str(body)
                    return body, None
            return None, "no canned response"
        return fake
    return _make


@pytest.fixture
def fake_service_check():
    """Patch service_check.check_service to return predictable statuses."""
    from utils.service_check import ServiceState

    def _make(states):
        def fake(name, port=None, host="localhost"):
            state, available = states.get(name, (ServiceState.UNKNOWN, False))
            status = MagicMock()
            status.state = state
            status.available = available
            status.message = f"{name}: {state.value}"
            status.port = port
            status.detection_method = "test"
            return status
        return fake
    return _make


# ──────────────────────────────────────────────────────────────────────
# Snapshot shape + uptime
# ──────────────────────────────────────────────────────────────────────


def test_snapshot_always_has_core_fields():
    """Even with everything offline, snapshot has host + generated_at + uptime."""
    with patch.object(fa, "_http_get_json", return_value=(None, "down")):
        snap = fa.collect_local_snapshot()
    assert snap.host == socket.gethostname()
    assert snap.generated_at > 0
    assert snap.uptime_s >= 0
    # Fields that may degrade still exist in the dataclass.
    assert isinstance(snap.services, dict)
    assert isinstance(snap.boundaries, dict)
    assert isinstance(snap.chat_recent, list)


def test_snapshot_to_dict_is_json_safe():
    """to_dict() output must round-trip through json.dumps."""
    with patch.object(fa, "_http_get_json", return_value=(None, "down")):
        snap = fa.collect_local_snapshot()
    payload = json.dumps(snap.to_dict())
    assert "host" in payload
    assert "generated_at" in payload


# ──────────────────────────────────────────────────────────────────────
# Services rollup
# ──────────────────────────────────────────────────────────────────────


def test_services_uses_known_services(fake_service_check):
    """KNOWN_SERVICES is enumerated and each entry contributes one row."""
    from utils.service_check import ServiceState, KNOWN_SERVICES

    states = {name: (ServiceState.AVAILABLE, True) for name in KNOWN_SERVICES}
    with patch("utils.service_check.check_service", side_effect=fake_service_check(states)), \
         patch.object(fa, "_http_get_json", return_value=(None, "down")):
        snap = fa.collect_local_snapshot()

    assert set(snap.services.keys()) == set(KNOWN_SERVICES.keys())
    for row in snap.services.values():
        assert row["state"] == "available"
        assert row["available"] is True


def test_services_swallows_individual_check_failures():
    """If check_service raises for one service, others still populate
    and the failed one ends up in services with state=unknown."""
    from utils.service_check import KNOWN_SERVICES

    def explode_only_rnsd(name, port=None, host="localhost"):
        if name == "rnsd":
            raise RuntimeError("systemctl crashed")
        s = MagicMock()
        s.state = MagicMock(value="available")
        s.available = True
        s.message = "ok"
        s.port = None
        s.detection_method = "test"
        return s

    with patch("utils.service_check.check_service", side_effect=explode_only_rnsd), \
         patch.object(fa, "_http_get_json", return_value=(None, "down")):
        snap = fa.collect_local_snapshot()

    assert snap.services["rnsd"]["state"] == "unknown"
    assert "systemctl crashed" in snap.services["rnsd"]["message"]
    # Other services were unaffected.
    other_names = [n for n in KNOWN_SERVICES if n != "rnsd"]
    assert all(snap.services[n]["available"] for n in other_names)


# ──────────────────────────────────────────────────────────────────────
# Daemon HTTP fetches: timeouts, bad bodies, success paths
# ──────────────────────────────────────────────────────────────────────


def test_daemon_health_failure_is_soft(mock_daemon_endpoints):
    fake = mock_daemon_endpoints({
        "/health": (None, "timeout: TimeoutError"),
        "/radio": ({"radio": {"connected": True}}, None),
        "/chat/messages": ({"count": 0, "messages": []}, None),
    })
    # The patched _http_get_json returns (body, err) — but our fake
    # above is shaped as a callable returning (body, err); patch directly.
    with patch.object(fa, "_http_get_json") as mock_get:
        def side(url, timeout=fa.DEFAULT_HTTP_TIMEOUT_S):
            if url.endswith("/health"):
                return None, "timeout"
            if url.endswith("/radio"):
                return {"radio": {"connected": True}}, None
            if url.endswith("/chat/messages"):
                return {"count": 0, "messages": []}, None
            return None, "no match"
        mock_get.side_effect = side
        snap = fa.collect_local_snapshot()

    assert snap.daemon_health is None
    sources = {e["source"] for e in snap.errors}
    assert "daemon_health" in sources
    # Successful sibling fetches still landed.
    assert snap.radio == {"connected": True}


def test_chat_caps_recent_to_limit():
    """Daemon may return more entries than CHAT_RECENT_LIMIT — we trim."""
    long_history = [{"id": i, "text": f"msg{i}"} for i in range(50)]
    with patch.object(fa, "_http_get_json") as mock_get:
        mock_get.side_effect = lambda url, timeout=2.0: (
            ({"count": 50, "messages": long_history}, None)
            if url.endswith("/chat/messages")
            else (None, "down")
        )
        snap = fa.collect_local_snapshot()

    assert snap.chat_total == 50
    assert len(snap.chat_recent) == fa.CHAT_RECENT_LIMIT
    # Newest tail.
    assert snap.chat_recent[-1]["id"] == 49


def test_chat_handles_non_dict_body():
    with patch.object(fa, "_http_get_json") as mock_get:
        mock_get.side_effect = lambda url, timeout=2.0: (
            ("garbage string", None)
            if url.endswith("/chat/messages") else (None, "down")
        )
        snap = fa.collect_local_snapshot()
    assert snap.chat_recent == []
    assert snap.chat_total == 0
    assert any(e["source"] == "chat" for e in snap.errors)


# ──────────────────────────────────────────────────────────────────────
# _http_get_json error handling
# ──────────────────────────────────────────────────────────────────────


def test_http_get_json_handles_url_error():
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.side_effect = urllib.error.URLError("connection refused")
        body, err = fa._http_get_json("http://127.0.0.1:8081/health")
    assert body is None
    assert err is not None
    assert "url error" in err


def test_http_get_json_handles_timeout():
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.side_effect = socket.timeout("read timed out")
        body, err = fa._http_get_json("http://127.0.0.1:8081/health")
    assert body is None
    assert "timeout" in err


def test_http_get_json_handles_bad_json():
    fake_resp = MagicMock()
    fake_resp.read.return_value = b"not json"
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=fake_resp):
        body, err = fa._http_get_json("http://127.0.0.1:8081/health")
    assert body is None
    assert "bad json" in err


# ──────────────────────────────────────────────────────────────────────
# Collector source
# ──────────────────────────────────────────────────────────────────────


def test_collector_stats_when_collector_supplied():
    fake_history = MagicMock()
    fake_history.get_stats.return_value = {"total_nodes": 42, "active_24h": 7}
    fake_collector = MagicMock()
    fake_collector._history = fake_history
    with patch.object(fa, "_http_get_json", return_value=(None, "down")):
        snap = fa.collect_local_snapshot(collector=fake_collector)
    assert snap.collector_stats == {"total_nodes": 42, "active_24h": 7}


def test_collector_stats_none_when_collector_omitted():
    with patch.object(fa, "_http_get_json", return_value=(None, "down")):
        snap = fa.collect_local_snapshot()
    assert snap.collector_stats is None


def test_collector_failure_is_soft():
    fake_history = MagicMock()
    fake_history.get_stats.side_effect = RuntimeError("bad sql")
    fake_collector = MagicMock()
    fake_collector._history = fake_history
    with patch.object(fa, "_http_get_json", return_value=(None, "down")):
        snap = fa.collect_local_snapshot(collector=fake_collector)
    assert snap.collector_stats is None
    assert any(e["source"] == "collector" for e in snap.errors)


# ──────────────────────────────────────────────────────────────────────
# Derived views
# ──────────────────────────────────────────────────────────────────────


def test_slo_view_handles_empty_snapshot():
    snap = fa.FleetSnapshot(generated_at=0, host="x", uptime_s=0)
    view = fa.slo_view(snap)
    assert view["overall_status"] == "unknown"
    assert view["services"]["total"] == 0
    assert view["boundaries_top"] == []
    assert view["radio"]["connected"] is False


def test_slo_view_uses_daemon_overall_status():
    snap = fa.FleetSnapshot(generated_at=0, host="x", uptime_s=0)
    snap.daemon_health = {"overall_status": "degraded", "services": []}
    snap.radio = {"connected": True, "name": "p4", "preset": "MeshCore-US-2", "battery_pct": 87}
    view = fa.slo_view(snap)
    assert view["overall_status"] == "degraded"
    assert view["radio"]["name"] == "p4"
    assert view["radio"]["battery_pct"] == 87


def test_slo_view_boundaries_top_sorted_by_count():
    snap = fa.FleetSnapshot(generated_at=0, host="x", uptime_s=0)
    snap.boundaries = {
        "rnsd.has_path": {"count": 100, "slow_count": 0, "error_count": 0,
                          "p50_s": 0.01, "p95_s": 0.02, "p99_s": 0.03, "samples": 100},
        "systemd.is_active": {"count": 500, "slow_count": 5, "error_count": 1,
                              "p50_s": 0.05, "p95_s": 0.10, "p99_s": 0.20, "samples": 500},
        "meshtasticd.send": {"count": 0, "slow_count": 0, "error_count": 0,
                             "p50_s": 0.0, "p95_s": 0.0, "p99_s": 0.0, "samples": 0},
    }
    view = fa.slo_view(snap)
    top = view["boundaries_top"]
    # Zero-count rows are dropped; remaining rows sorted desc by count.
    assert [r["label"] for r in top] == ["systemd.is_active", "rnsd.has_path"]
    assert top[0]["error_rate"] == pytest.approx(1 / 500)


def test_activity_view_includes_chat_and_slow_boundaries():
    snap = fa.FleetSnapshot(generated_at=0, host="x", uptime_s=0)
    snap.chat_recent = [{"id": 1, "text": "hi"}]
    snap.chat_total = 1
    snap.boundaries = {
        "rnsd.has_path": {"count": 50, "slow_count": 3, "error_count": 0,
                          "p50_s": 0.01, "p95_s": 0.02, "p99_s": 0.05, "samples": 50},
        "rnsd.handle_outbound": {"count": 10, "slow_count": 0, "error_count": 2,
                                 "p50_s": 0.1, "p95_s": 0.5, "p99_s": 1.0, "samples": 10},
    }
    view = fa.activity_view(snap)
    assert view["chat_total"] == 1
    assert view["chat_recent"] == [{"id": 1, "text": "hi"}]
    labels = [row["label"] for row in view["slow_boundaries"]]
    # Errors rank above slow-only.
    assert labels[0] == "rnsd.handle_outbound"
    assert "rnsd.has_path" in labels


def test_include_daemon_health_false_skips_health_fetch():
    """SLO and activity endpoints pass include_daemon_health=False to
    avoid the slow daemon /health fetch (~2s serial systemctl walk).
    Other daemon fetches (radio, chat) still happen."""
    seen = []
    def track(url, timeout=2.0):
        seen.append(url)
        if url.endswith("/radio"):
            return {"radio": {"connected": False}}, None
        if url.endswith("/chat/messages"):
            return {"count": 0, "messages": []}, None
        return None, "should not be called"
    with patch.object(fa, "_http_get_json", side_effect=track):
        snap = fa.collect_local_snapshot(include_daemon_health=False)
    assert not any(u.endswith("/health") for u in seen)
    # Sibling fetches still happened.
    assert snap.daemon_health is None
    assert any(u.endswith("/radio") for u in seen)
    assert any(u.endswith("/chat/messages") for u in seen)


def test_slo_view_derives_overall_when_daemon_health_absent():
    """When include_daemon_health=False is used, slo_view falls back to
    the local services rollup so the dashboard gets a useful status
    field instead of 'unknown'."""
    snap = fa.FleetSnapshot(generated_at=0, host="x", uptime_s=0)
    snap.services = {
        "rnsd": {"available": True, "state": "available"},
        "meshtasticd": {"available": True, "state": "available"},
        "meshcore-radio": {"available": False, "state": "not_running"},
    }
    view = fa.slo_view(snap)
    assert view["overall_status"] == "degraded"

    # All-up flips to ready.
    snap.services["meshcore-radio"]["available"] = True
    view = fa.slo_view(snap)
    assert view["overall_status"] == "ready"

    # Daemon health, when present, takes precedence.
    snap.daemon_health = {"overall_status": "error"}
    view = fa.slo_view(snap)
    assert view["overall_status"] == "error"


def test_activity_view_drops_clean_boundaries():
    """Boundaries with zero slow_count and zero error_count don't surface
    on the activity feed — the feed is for anomalies only."""
    snap = fa.FleetSnapshot(generated_at=0, host="x", uptime_s=0)
    snap.boundaries = {
        "rnsd.has_path": {"count": 100, "slow_count": 0, "error_count": 0,
                          "p50_s": 0.01, "p95_s": 0.02, "p99_s": 0.03, "samples": 100},
    }
    view = fa.activity_view(snap)
    assert view["slow_boundaries"] == []
