"""Tests for monitoring.fleet_rollup — multi-host composer.

Mirrors the never-raise contract from `fleet_aggregator`. The rollup
must always return a `FleetRollup` that the dashboard can render —
peer fetch errors land in per-row `error` fields, federation
filtering degrades to empty when the directory is unavailable.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from monitoring.fleet_config import FleetConfig, FederationConfig, PeerConfig
from monitoring import fleet_rollup as fr


def _make_config(peers=None, federation=None):
    cfg = FleetConfig()
    if peers:
        cfg.peers = list(peers)
    if federation is not None:
        cfg.federation = federation
    cfg.source_path = "/tmp/test-fleet.json"
    return cfg


# ──────────────────────────────────────────────────────────────────────
# Self-only path (no peers)
# ──────────────────────────────────────────────────────────────────────


def test_empty_config_returns_self_only():
    """Empty fleet.json — rollup has self snapshot, no peers, no
    federation peers when the collector is None."""
    with patch("monitoring.fleet_aggregator._http_get_json",
               return_value=(None, "down")):
        rollup = fr.collect_fleet_rollup(_make_config())
    assert rollup.peers == []
    assert rollup.federation_peers == []
    assert isinstance(rollup.self_snapshot, dict)
    # generated_at + self_host always populate.
    assert rollup.generated_at > 0
    assert rollup.self_host


def test_parse_error_surfaces_in_errors():
    cfg = _make_config()
    cfg.parse_error = "deliberate test"
    with patch("monitoring.fleet_aggregator._http_get_json",
               return_value=(None, "down")):
        rollup = fr.collect_fleet_rollup(cfg)
    assert any(e["source"] == "config" and "deliberate" in e["error"]
               for e in rollup.errors)


# ──────────────────────────────────────────────────────────────────────
# Peer fetch
# ──────────────────────────────────────────────────────────────────────


def test_peer_fetch_happy_path():
    peer_snap = {
        "host": "remote",
        "overall_status": "ready",
        "services": {"total": 1, "available": 1, "by_state": {"available": 1}},
        "radio": {"connected": True},
    }
    cfg = _make_config(peers=[
        PeerConfig(name="remote", host="remote.example", port=5001, kind="noc"),
    ])

    def fake_http(url, timeout=3.0):
        if url == "http://remote.example:5001/fleet/slo":
            return peer_snap, None
        return None, "unexpected url"

    with patch("monitoring.fleet_aggregator._http_get_json", side_effect=fake_http):
        rollup = fr.collect_fleet_rollup(cfg, self_url=None)

    assert len(rollup.peers) == 1
    row = rollup.peers[0]
    assert row.name == "remote"
    assert row.snapshot == peer_snap
    assert row.error is None
    assert row.fetch_duration_s >= 0


def test_peer_fetch_failure_is_per_row():
    cfg = _make_config(peers=[
        PeerConfig(name="alive", host="alive.example", port=5001),
        PeerConfig(name="dead", host="dead.example", port=5001),
    ])

    def fake_http(url, timeout=3.0):
        if "alive.example" in url and "/fleet/slo" in url:
            return {"ok": True}, None
        return None, "timeout"

    with patch("monitoring.fleet_aggregator._http_get_json", side_effect=fake_http):
        rollup = fr.collect_fleet_rollup(cfg)

    assert len(rollup.peers) == 2
    by_name = {p.name: p for p in rollup.peers}
    assert by_name["alive"].snapshot == {"ok": True}
    assert by_name["alive"].error is None
    assert by_name["dead"].snapshot is None
    assert by_name["dead"].error == "timeout"


def test_peer_skipped_when_host_matches_self():
    cfg = _make_config(peers=[
        PeerConfig(name="self", host="MyHost", port=5001),
        PeerConfig(name="other", host="other.example", port=5001),
    ])
    with patch("monitoring.fleet_aggregator._http_get_json",
               return_value=({"ok": True}, None)), \
         patch("socket.gethostname", return_value="MyHost"):
        rollup = fr.collect_fleet_rollup(cfg)
    assert [p.name for p in rollup.peers] == ["other"]


def test_self_url_override_routes_self_through_http():
    """When tests pass `self_url`, the local source is fetched the
    same way peers are — useful for end-to-end behavior tests."""
    seen = []

    def fake_http(url, timeout=3.0):
        seen.append(url)
        return {"host": "self-via-http"}, None

    with patch("monitoring.fleet_aggregator._http_get_json", side_effect=fake_http):
        rollup = fr.collect_fleet_rollup(
            _make_config(),
            self_url="http://127.0.0.1:5001/fleet/health",
        )
    assert "http://127.0.0.1:5001/fleet/health" in seen
    assert rollup.self_snapshot == {"host": "self-via-http"}


# ──────────────────────────────────────────────────────────────────────
# Federation peer view
# ──────────────────────────────────────────────────────────────────────


def _make_collector(features=None, position_less=None):
    history = MagicMock()
    history.get_directory_snapshot.return_value = (features or [], position_less or [])
    collector = MagicMock()
    collector._history = history
    return collector


def test_federation_filters_to_rns_network():
    collector = _make_collector(position_less=[
        {"id": "moc", "name": "moc-gateway", "network": "rns",
         "last_seen": 100.0, "last_seen_age_s": 10.0,
         "source_origin": "rns_announce"},
        {"id": "abc", "name": "meshtastic-node", "network": "meshtastic",
         "last_seen": 100.0, "last_seen_age_s": 5.0,
         "source_origin": "mqtt"},
    ])
    cfg = _make_config(federation=FederationConfig(fresh_window_s=3600))
    with patch("monitoring.fleet_aggregator._http_get_json",
               return_value=(None, "down")):
        rollup = fr.collect_fleet_rollup(cfg, collector=collector)
    names = [p.name for p in rollup.federation_peers]
    assert names == ["moc-gateway"]


def test_federation_drops_stale_peers_outside_fresh_window():
    collector = _make_collector(position_less=[
        {"id": "fresh", "name": "fresh-peer", "network": "rns",
         "last_seen_age_s": 100.0, "source_origin": "rns"},
        {"id": "stale", "name": "stale-peer", "network": "rns",
         "last_seen_age_s": 99999.0, "source_origin": "rns"},
    ])
    cfg = _make_config(federation=FederationConfig(fresh_window_s=3600))
    with patch("monitoring.fleet_aggregator._http_get_json",
               return_value=(None, "down")):
        rollup = fr.collect_fleet_rollup(cfg, collector=collector)
    assert [p.name for p in rollup.federation_peers] == ["fresh-peer"]


def test_federation_sorted_by_freshness():
    collector = _make_collector(position_less=[
        {"id": "old", "name": "old", "network": "rns",
         "last_seen_age_s": 600.0, "source_origin": "rns"},
        {"id": "new", "name": "new", "network": "rns",
         "last_seen_age_s": 30.0, "source_origin": "rns"},
        {"id": "mid", "name": "mid", "network": "rns",
         "last_seen_age_s": 200.0, "source_origin": "rns"},
    ])
    cfg = _make_config(federation=FederationConfig(fresh_window_s=3600))
    with patch("monitoring.fleet_aggregator._http_get_json",
               return_value=(None, "down")):
        rollup = fr.collect_fleet_rollup(cfg, collector=collector)
    assert [p.name for p in rollup.federation_peers] == ["new", "mid", "old"]


def test_federation_disabled_returns_empty():
    collector = _make_collector(position_less=[
        {"id": "a", "name": "a", "network": "rns",
         "last_seen_age_s": 5.0, "source_origin": "rns"},
    ])
    cfg = _make_config(federation=FederationConfig(scrape_rns_announces=False))
    with patch("monitoring.fleet_aggregator._http_get_json",
               return_value=(None, "down")):
        rollup = fr.collect_fleet_rollup(cfg, collector=collector)
    assert rollup.federation_peers == []


def test_federation_handles_missing_collector():
    cfg = _make_config(federation=FederationConfig())
    with patch("monitoring.fleet_aggregator._http_get_json",
               return_value=(None, "down")):
        rollup = fr.collect_fleet_rollup(cfg, collector=None)
    assert rollup.federation_peers == []


def test_federation_handles_directory_exception():
    history = MagicMock()
    history.get_directory_snapshot.side_effect = RuntimeError("bad sql")
    collector = MagicMock()
    collector._history = history
    cfg = _make_config(federation=FederationConfig())
    with patch("monitoring.fleet_aggregator._http_get_json",
               return_value=(None, "down")):
        rollup = fr.collect_fleet_rollup(cfg, collector=collector)
    # Soft-fail to empty list — the rest of the rollup still renders.
    assert rollup.federation_peers == []


# ──────────────────────────────────────────────────────────────────────
# Daemon-side federation registry — canonical source
# ──────────────────────────────────────────────────────────────────────


def test_federation_includes_daemon_registry_peers():
    """Daemon's /fleet/federation returns the live RNS announce registry.
    Without this, the rollup only sees directory cache (empty on
    meshanchor-server) — the regression observed 2026-05-11 where
    /fleet/federation directly showed 17 peers but
    rollup.federation_peers showed 0.
    """
    daemon_body = {"peers": [
        {"hash_hex": "abc123", "name": "moc-MF-gateway",
         "last_seen": 1000.0, "last_seen_age_s": 30.0},
        {"hash_hex": "def456", "name": "remote-nomadnet",
         "last_seen": 999.0, "last_seen_age_s": 75.0},
    ]}
    cfg = _make_config(federation=FederationConfig(fresh_window_s=3600))

    def fake_http(url, timeout=3.0):
        if "/fleet/federation" in url:
            return daemon_body, None
        return None, "no match"

    with patch("monitoring.fleet_aggregator._http_get_json", side_effect=fake_http):
        rollup = fr.collect_fleet_rollup(cfg, collector=None)

    names = [p.name for p in rollup.federation_peers]
    assert names == ["moc-MF-gateway", "remote-nomadnet"]
    assert all(p.network == "rns" for p in rollup.federation_peers)
    assert all(p.source_origin == "rns_announce" for p in rollup.federation_peers)


def test_federation_unions_daemon_and_directory_dedup_by_node_id():
    """Daemon and directory may both surface the same peer. The daemon
    registry wins (it's the canonical source); the directory contributes
    extras."""
    daemon_body = {"peers": [
        {"hash_hex": "shared", "name": "shared-daemon-name",
         "last_seen": 1000.0, "last_seen_age_s": 20.0},
    ]}
    collector = _make_collector(position_less=[
        {"id": "shared", "name": "shared-directory-name", "network": "rns",
         "last_seen_age_s": 25.0, "source_origin": "rns_path_table"},
        {"id": "directory_only", "name": "dir-extra", "network": "rns",
         "last_seen_age_s": 40.0, "source_origin": "rns_path_table"},
    ])
    cfg = _make_config(federation=FederationConfig(fresh_window_s=3600))

    def fake_http(url, timeout=3.0):
        if "/fleet/federation" in url:
            return daemon_body, None
        return None, "no match"

    with patch("monitoring.fleet_aggregator._http_get_json", side_effect=fake_http):
        rollup = fr.collect_fleet_rollup(cfg, collector=collector)

    by_id = {p.node_id: p for p in rollup.federation_peers}
    # Both peers present.
    assert set(by_id) == {"shared", "directory_only"}
    # Daemon won for `shared` — the daemon-supplied name beats the directory.
    assert by_id["shared"].name == "shared-daemon-name"
    assert by_id["shared"].source_origin == "rns_announce"


def test_federation_daemon_failure_falls_back_to_directory():
    """If the daemon doesn't answer (down, timeout), the rollup still
    returns whatever the directory has — degradation, not silence."""
    collector = _make_collector(position_less=[
        {"id": "only-in-dir", "name": "directory-peer", "network": "rns",
         "last_seen_age_s": 50.0, "source_origin": "rns_path_table"},
    ])
    cfg = _make_config(federation=FederationConfig(fresh_window_s=3600))

    with patch("monitoring.fleet_aggregator._http_get_json",
               return_value=(None, "down")):
        rollup = fr.collect_fleet_rollup(cfg, collector=collector)

    names = [p.name for p in rollup.federation_peers]
    assert names == ["directory-peer"]


def test_federation_daemon_respects_fresh_window():
    """Peers outside the freshness window are dropped, even from the
    daemon registry."""
    daemon_body = {"peers": [
        {"hash_hex": "fresh", "name": "fresh-peer",
         "last_seen_age_s": 100.0},
        {"hash_hex": "stale", "name": "stale-peer",
         "last_seen_age_s": 99999.0},
    ]}
    cfg = _make_config(federation=FederationConfig(fresh_window_s=3600))

    def fake_http(url, timeout=3.0):
        if "/fleet/federation" in url:
            return daemon_body, None
        return None, "no match"

    with patch("monitoring.fleet_aggregator._http_get_json", side_effect=fake_http):
        rollup = fr.collect_fleet_rollup(cfg, collector=None)

    assert [p.name for p in rollup.federation_peers] == ["fresh-peer"]


def test_federation_daemon_uses_hash_hex_when_name_missing():
    """Daemon entries without a `name` fall back to the hash as the
    display name — never silently drop a real announce."""
    daemon_body = {"peers": [
        {"hash_hex": "deadbeef1234", "last_seen_age_s": 10.0},
    ]}
    cfg = _make_config(federation=FederationConfig(fresh_window_s=3600))

    def fake_http(url, timeout=3.0):
        if "/fleet/federation" in url:
            return daemon_body, None
        return None, "no match"

    with patch("monitoring.fleet_aggregator._http_get_json", side_effect=fake_http):
        rollup = fr.collect_fleet_rollup(cfg, collector=None)

    assert [p.name for p in rollup.federation_peers] == ["deadbeef1234"]


# ──────────────────────────────────────────────────────────────────────
# Serialization
# ──────────────────────────────────────────────────────────────────────


def test_to_dict_is_json_safe():
    with patch("monitoring.fleet_aggregator._http_get_json",
               return_value=(None, "down")):
        rollup = fr.collect_fleet_rollup(_make_config())
    payload = json.dumps(rollup.to_dict())
    assert "self_host" in payload
    assert "peers" in payload
