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
    # Successful sibling fetches still landed. `_collect_radio` now
    # normalizes the daemon body into the slo_view contract — the
    # explicit `connected=True` in the input still surfaces, plus
    # the contract's nullable fields.
    assert snap.radio == {
        "connected": True, "name": None, "preset": None, "battery_pct": None,
    }


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


# ──────────────────────────────────────────────────────────────────────
# _radio_slo_shape — daemon body → slo_view contract
# ──────────────────────────────────────────────────────────────────────


def test_radio_slo_shape_meshcore_full_body_is_connected():
    """Daemon publishes a fully-populated MeshCore body after a live
    serial handshake. `radio_freq_mhz` non-null IS the connected signal.
    Regression: 2026-05-11 fleet rollup showed connected=False despite
    a healthy 910.525 MHz radio on meshanchor-server."""
    raw = {
        "radio_freq_mhz": 910.525,
        "radio_bw_khz": 62.5,
        "radio_sf": 7,
        "radio_cr": 5,
        "node_name": "meshanchorRAK1",
        "tx_power_dbm": 22,
    }
    view = fa._radio_slo_shape(raw)
    assert view["connected"] is True
    assert view["name"] == "meshanchorRAK1"
    assert view["preset"] == "BW62/SF7"
    assert view["battery_pct"] is None


def test_radio_slo_shape_all_null_body_is_disconnected():
    """Daemon reachable but no radio attached — every field null."""
    raw = {
        "radio_freq_mhz": None, "radio_bw_khz": None, "radio_sf": None,
        "node_name": None, "tx_power_dbm": None,
    }
    view = fa._radio_slo_shape(raw)
    assert view["connected"] is False
    assert view["name"] is None
    assert view["preset"] is None


def test_radio_slo_shape_explicit_connected_field_wins():
    """Forward-compat: if the daemon starts emitting `connected`, honor it."""
    raw = {"connected": True, "name": "explicit-name"}
    view = fa._radio_slo_shape(raw)
    assert view["connected"] is True
    assert view["name"] == "explicit-name"


def test_radio_slo_shape_preset_skipped_when_modem_params_partial():
    """Don't fabricate a preset string if either bw or sf is missing."""
    raw = {"radio_freq_mhz": 910.525, "radio_bw_khz": 62.5}  # no sf
    view = fa._radio_slo_shape(raw)
    assert view["connected"] is True
    assert view["preset"] is None


def test_radio_slo_shape_uses_preset_passthrough_if_present():
    """If a future daemon publishes a preset string, pass it through."""
    raw = {"radio_freq_mhz": 910.525, "preset": "MeshCore-US-2"}
    view = fa._radio_slo_shape(raw)
    assert view["preset"] == "MeshCore-US-2"


def test_radio_slo_shape_battery_pct_passed_through():
    raw = {"radio_freq_mhz": 910.525, "battery_pct": 87}
    view = fa._radio_slo_shape(raw)
    assert view["battery_pct"] == 87


# ──────────────────────────────────────────────────────────────────────
# _collect_radio — multi-stack fallback for MA-on-Meshtastic hosts
# ──────────────────────────────────────────────────────────────────────


def test_collect_radio_prefers_meshcore_when_daemon_populated():
    """MeshCore handshake wins over the meshtasticd fallback. A real
    MeshCore-equipped MA box must not be reported as 'meshtasticd' just
    because :4403 happens to also be alive."""
    daemon_body = {"radio": {
        "radio_freq_mhz": 910.525, "radio_bw_khz": 62.5, "radio_sf": 7,
        "node_name": "meshanchorRAK1",
    }}
    with patch.object(fa, "_http_get_json", return_value=(daemon_body, None)), \
         patch.object(fa, "_tcp_listener_alive", return_value=True), \
         patch("os.path.exists", return_value=False):
        view, err = fa._collect_radio("http://x", timeout=1.0)
    assert err is None
    assert view["connected"] is True
    assert view["name"] == "meshanchorRAK1"


def test_collect_radio_falls_back_to_meshtasticd_when_daemon_empty():
    """The VolcanoAI case. MA daemon's /radio body has no MeshCore data
    (all-null fields, no live handshake), but local meshtasticd is
    answering on :4403. Report the Meshtastic stack instead of
    falsely flagging the box as radioless."""
    daemon_body = {"radio": {
        "radio_freq_mhz": None, "radio_bw_khz": None, "radio_sf": None,
        "node_name": None,
    }}
    with patch.object(fa, "_http_get_json", return_value=(daemon_body, None)), \
         patch.object(fa, "_tcp_listener_alive", return_value=True), \
         patch("os.path.exists", return_value=False):
        view, err = fa._collect_radio("http://x", timeout=1.0)
    assert err is None
    assert view == {
        "connected": True, "name": "meshtasticd",
        "preset": None, "battery_pct": None,
    }


def test_collect_radio_falls_back_to_meshcore_tty_when_only_symlink():
    """MeshCore radio plugged in but daemon hasn't yet handshook. The
    /dev/ttyMeshCore symlink is sufficient evidence the box owns a radio."""
    daemon_body = {"radio": {"radio_freq_mhz": None, "node_name": None}}
    with patch.object(fa, "_http_get_json", return_value=(daemon_body, None)), \
         patch.object(fa, "_tcp_listener_alive", return_value=False), \
         patch("os.path.exists", return_value=True) as mock_exists:
        view, err = fa._collect_radio("http://x", timeout=1.0)
    assert err is None
    assert view["connected"] is True
    assert view["name"] == "meshcore"
    mock_exists.assert_any_call(fa.MESHCORE_TTY)


def test_collect_radio_disconnected_when_no_stack_responds():
    """Pure-NOC daemon: no MeshCore handshake, :4403 silent, no symlink.
    The chip-firing case — report connected=False so the dashboard can
    raise a real anomaly."""
    daemon_body = {"radio": {"radio_freq_mhz": None, "node_name": None}}
    with patch.object(fa, "_http_get_json", return_value=(daemon_body, None)), \
         patch.object(fa, "_tcp_listener_alive", return_value=False), \
         patch("os.path.exists", return_value=False):
        view, err = fa._collect_radio("http://x", timeout=1.0)
    assert err is None
    assert view["connected"] is False
    assert view["name"] is None


def test_collect_radio_daemon_error_still_probes_fallbacks():
    """When MA's /radio HTTP fetch fails outright (daemon down, timeout)
    the fallback probes still run — the operator's box may still have
    a working Meshtastic stack. If :4403 answers, that wins; otherwise
    the original daemon error surfaces."""
    with patch.object(fa, "_http_get_json", return_value=(None, "url error: refused")), \
         patch.object(fa, "_tcp_listener_alive", return_value=True), \
         patch("os.path.exists", return_value=False):
        view, err = fa._collect_radio("http://x", timeout=1.0)
    assert err is None
    assert view["name"] == "meshtasticd"

    with patch.object(fa, "_http_get_json", return_value=(None, "url error: refused")), \
         patch.object(fa, "_tcp_listener_alive", return_value=False), \
         patch("os.path.exists", return_value=False):
        view, err = fa._collect_radio("http://x", timeout=1.0)
    assert err is not None
    assert err["source"] == "radio"


def test_collect_radio_explicit_connected_skips_fallback():
    """Forward-compat: if a future MA daemon sets radio.connected=True
    even without filled config (e.g. an early-handshake state), trust it
    and don't shadow the name by jumping to the meshtasticd fallback."""
    daemon_body = {"radio": {"connected": True, "name": "early-state"}}
    with patch.object(fa, "_http_get_json", return_value=(daemon_body, None)), \
         patch.object(fa, "_tcp_listener_alive", return_value=True), \
         patch("os.path.exists", return_value=True):
        view, err = fa._collect_radio("http://x", timeout=1.0)
    assert err is None
    assert view["name"] == "early-state"


def test_tcp_listener_alive_handles_timeout_and_refusal(monkeypatch):
    """Refused / timeout / OSError all return False without raising."""
    class FakeSock:
        def __init__(self, *args, **kwargs):
            self._behavior = FakeSock.behavior
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def settimeout(self, _): pass
        def connect_ex(self, _):
            if self._behavior == "refused":
                return 111  # ECONNREFUSED
            if self._behavior == "ok":
                return 0
            raise socket.timeout("read timed out")

    FakeSock.behavior = "ok"
    monkeypatch.setattr(socket, "socket", FakeSock)
    assert fa._tcp_listener_alive("127.0.0.1", 4403) is True

    FakeSock.behavior = "refused"
    assert fa._tcp_listener_alive("127.0.0.1", 4403) is False

    FakeSock.behavior = "timeout"
    assert fa._tcp_listener_alive("127.0.0.1", 4403) is False


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


def test_services_rollup_splits_required_and_optional():
    """`_services_rollup` must expose required/optional sub-counts so the
    dashboard can read 2/2 (required available) instead of 2/6 when the
    Meshtastic-side / supervisor optional services are down on a
    MeshCore-primary NOC. The legacy `total` / `available` / `by_state`
    fields stay populated for back-compat with the SQLite history
    schema."""
    services = {
        # Required
        "rnsd":            {"available": True,  "state": "available",   "optional": False},
        "mosquitto":       {"available": True,  "state": "available",   "optional": False},
        # Optional, all down
        "meshtasticd":     {"available": False, "state": "not_running", "optional": True},
        "nomadnet":        {"available": False, "state": "not_running", "optional": True},
        "meshtasticd-alt": {"available": False, "state": "not_running", "optional": True},
        "meshcore-radio":  {"available": False, "state": "not_running", "optional": True},
    }
    rollup = fa._services_rollup(services)
    # Legacy totals — unchanged shape, count everything.
    assert rollup["total"] == 6
    assert rollup["available"] == 2
    assert rollup["by_state"] == {"available": 2, "not_running": 4}
    # Required slice — what the headline reads from.
    assert rollup["required"]["total"] == 2
    assert rollup["required"]["available"] == 2
    assert rollup["required"]["by_state"] == {"available": 2}
    # Optional slice — rendered separately, doesn't gate the SLO.
    assert rollup["optional"]["total"] == 4
    assert rollup["optional"]["available"] == 0
    assert rollup["optional"]["by_state"] == {"not_running": 4}


def test_overall_status_ignores_optional_services_when_down():
    """A MeshCore-primary NOC where every required service is up but
    every optional service is down should read `ready`, not `degraded`.
    Before option-2, `_derive_overall_status` counted all services and
    flagged this state as degraded forever."""
    snap = fa.FleetSnapshot(generated_at=0, host="x", uptime_s=0)
    snap.services = {
        "rnsd":            {"available": True,  "state": "available",   "optional": False},
        "mosquitto":       {"available": True,  "state": "available",   "optional": False},
        "meshtasticd":     {"available": False, "state": "not_running", "optional": True},
        "meshcore-radio":  {"available": False, "state": "not_running", "optional": True},
    }
    assert fa._derive_overall_status(snap) == "ready"

    # Required goes down → degraded (not error — at least one still up).
    snap.services["rnsd"]["available"] = False
    snap.services["rnsd"]["state"] = "not_running"
    assert fa._derive_overall_status(snap) == "degraded"

    # All required down → error, regardless of optionals.
    snap.services["mosquitto"]["available"] = False
    snap.services["mosquitto"]["state"] = "not_running"
    assert fa._derive_overall_status(snap) == "error"


def test_overall_status_falls_back_when_all_services_optional():
    """Pathological config with every service marked optional must not
    crash and must not always return ready — fall back to full-set
    semantics so the rollup stays meaningful."""
    snap = fa.FleetSnapshot(generated_at=0, host="x", uptime_s=0)
    snap.services = {
        "a": {"available": False, "state": "not_running", "optional": True},
        "b": {"available": False, "state": "not_running", "optional": True},
    }
    assert fa._derive_overall_status(snap) == "error"


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


# ─── _list_timers_scope: root→operator drop ─────────────────────────────
#
# meshanchor-map.service runs as User=root on meshanchor-server; root
# has no /run/user/0/bus, so `systemctl --user list-timers` from root
# only sees system timers. The drop mirrors fire_unit's c6d7609 fix —
# `sudo -n -u <op> env XDG_RUNTIME_DIR=/run/user/<uid> systemctl --user
# list-timers ...`. Closes the schedules-panel under-report on
# meshanchor-server (only meshanchor-daemon-restart showed when
# wh6gxz has 3+ active meshforge-* user timers).


def _capture_subprocess_run(returncode: int = 0, stdout: str = "[]"):
    captured = {}

    def _fake(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return MagicMock(returncode=returncode, stdout=stdout, stderr="")

    return _fake, captured


def test_list_timers_non_root_user_scope_injects_xdg(monkeypatch):
    """Non-root daemon path: inject XDG_RUNTIME_DIR, plain systemctl."""
    fake, cap = _capture_subprocess_run()
    monkeypatch.setattr("monitoring.fleet_aggregator.os.geteuid", lambda: 1000)
    monkeypatch.setattr(
        "monitoring.fleet_aggregator.os.environ",
        {k: v for k, v in __import__("os").environ.items() if k != "XDG_RUNTIME_DIR"},
    )
    monkeypatch.setattr("monitoring.fleet_aggregator.subprocess.run", fake)
    fa._list_timers_scope("user")
    assert cap["cmd"][0] == "systemctl"
    assert "--user" in cap["cmd"]
    assert "sudo" not in cap["cmd"]
    assert cap["env"]["XDG_RUNTIME_DIR"] == "/run/user/1000"


def test_list_timers_root_user_scope_drops_to_operator(monkeypatch):
    """Root + user scope: drop privilege to operator. The
    meshanchor-server case — daemon User=root, operator wh6gxz."""
    fake, cap = _capture_subprocess_run()
    monkeypatch.setattr("monitoring.fleet_aggregator.os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "utils.fleet_test_runner._find_operator_user",
        lambda: (1000, "wh6gxz"),
    )
    monkeypatch.setattr("monitoring.fleet_aggregator.subprocess.run", fake)
    fa._list_timers_scope("user")
    assert cap["cmd"][0] == "sudo"
    assert "-n" in cap["cmd"]
    assert cap["cmd"][cap["cmd"].index("-u") + 1] == "wh6gxz"
    env_idx = cap["cmd"].index("env")
    assert cap["cmd"][env_idx + 1] == "XDG_RUNTIME_DIR=/run/user/1000"
    assert "systemctl" in cap["cmd"]
    assert "--user" in cap["cmd"]
    assert "list-timers" in cap["cmd"]


def test_list_timers_root_system_scope_stays_plain(monkeypatch):
    """Root + system scope: no drop, no --user, no sudo."""
    fake, cap = _capture_subprocess_run()
    monkeypatch.setattr("monitoring.fleet_aggregator.os.geteuid", lambda: 0)
    monkeypatch.setattr("monitoring.fleet_aggregator.subprocess.run", fake)
    fa._list_timers_scope("system")
    assert cap["cmd"][0] == "systemctl"
    assert "--user" not in cap["cmd"]
    assert "sudo" not in cap["cmd"]


def test_list_timers_root_user_scope_no_operator_returns_empty(monkeypatch):
    """No candidate UID in /run/user/ → return [] without invoking
    subprocess. Avoids cryptic root-bus errors leaking into the panel."""

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("subprocess.run must not be invoked")

    monkeypatch.setattr("monitoring.fleet_aggregator.os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "utils.fleet_test_runner._find_operator_user", lambda: None,
    )
    monkeypatch.setattr(
        "monitoring.fleet_aggregator.subprocess.run", _should_not_be_called,
    )
    assert fa._list_timers_scope("user") == []


# ─── CI status block ────────────────────────────────────────────────────
#
# Mirror of MF fleet_snapshot CI status tests. The block flows to the
# dashboard pill via slo_view → MA's rollup → fleet.html.


_CI_FILE_SAMPLE = (
    "# MeshForge ecosystem CI status — generated 2026-05-15T08:04:36-10:00\n"
    "  meshforge                            in_progress  a11095c  feat(fleet): T1.5\n"
    "  meshanchor                           in_progress  3fbf241  feat(fleet): panel\n"
    "  meshforge-maps                       success      0ec25c8  fix(tests): foo\n"
    "  meshing_around_meshforge             success      3d1c97b  github_actions\n"
    "  RNS-Management-Tool                  success      dc1b109  Merge pull request\n"
    "  RNS-Meshtastic-Gateway-Tool          success      cd2748a  fix(ci): drop -x\n"
)


def test_parse_ci_status_file_extracts_repos_and_overall():
    block = fa._parse_ci_status_file(_CI_FILE_SAMPLE)
    assert block["available"] is True
    assert block["generated_at"] == "2026-05-15T08:04:36-10:00"
    assert isinstance(block["generated_unix"], float)
    assert len(block["repos"]) == 6
    assert block["overall"] == "in_progress"
    assert block["in_progress_count"] == 2
    assert block["red_count"] == 0


def test_parse_ci_status_file_ignores_overdue_pr_section():
    sample = (
        _CI_FILE_SAMPLE
        + "\n# Overdue open PRs (>14 days)\n"
        + "  meshforge#1234  20d  user — Some title\n"
    )
    block = fa._parse_ci_status_file(sample)
    assert len(block["repos"]) == 6


def test_parse_ci_status_file_handles_no_runs_state():
    sample = (
        "# generated 2026-05-15T08:04:36-10:00\n"
        "  newrepo  no-runs\n"
    )
    block = fa._parse_ci_status_file(sample)
    assert len(block["repos"]) == 1
    assert block["repos"][0]["state"] == "no-runs"
    assert block["repos"][0]["sha"] == ""


def test_ci_overall_failure_dominates():
    repos = [
        {"name": "a", "state": "success", "sha": "1234567"},
        {"name": "b", "state": "failure", "sha": "1234567"},
    ]
    assert fa._ci_overall(repos) == "failure"


def test_ci_overall_in_progress_when_no_failure():
    repos = [
        {"name": "a", "state": "success", "sha": "1234567"},
        {"name": "b", "state": "in_progress", "sha": "1234567"},
    ]
    assert fa._ci_overall(repos) == "in_progress"


def test_ci_overall_success_when_all_clean():
    repos = [{"name": "a", "state": "success", "sha": "1234567"}]
    assert fa._ci_overall(repos) == "success"


def test_ci_overall_unknown_when_no_repos():
    assert fa._ci_overall([]) == "unknown"


def test_ci_status_block_unavailable_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(fa, "_operator_home", lambda: tmp_path)
    block = fa._ci_status_block()
    assert block["available"] is False
    assert block["reason"] == "no_file"


def test_ci_status_block_unavailable_when_no_operator_home(monkeypatch):
    monkeypatch.setattr(fa, "_operator_home", lambda: None)
    block = fa._ci_status_block()
    assert block["available"] is False
    assert block["reason"] == "no_operator_home"


def test_ci_status_block_parses_real_file(tmp_path, monkeypatch):
    (tmp_path / ".meshforge-ci-status").write_text(_CI_FILE_SAMPLE)
    monkeypatch.setattr(fa, "_operator_home", lambda: tmp_path)
    block = fa._ci_status_block()
    assert block["available"] is True
    assert block["overall"] == "in_progress"
    assert len(block["repos"]) == 6


def test_ci_status_block_marks_stale_when_old(tmp_path, monkeypatch):
    sample = (
        "# MeshForge ecosystem CI status — generated 2020-01-01T00:00:00-10:00\n"
        "  meshforge  success  abc1234  ok\n"
    )
    (tmp_path / ".meshforge-ci-status").write_text(sample)
    monkeypatch.setattr(fa, "_operator_home", lambda: tmp_path)
    block = fa._ci_status_block()
    assert block["available"] is True
    assert block["stale"] is True


def test_slo_view_includes_ci_status_block():
    """Contract: slo_view's output must carry ci_status so MA's rollup
    poller threads the data through to the dashboard pill."""
    snap = fa.FleetSnapshot(generated_at=0, host="x", uptime_s=0)
    view = fa.slo_view(snap)
    assert "ci_status" in view
    assert isinstance(view["ci_status"], dict)
    assert "available" in view["ci_status"]
