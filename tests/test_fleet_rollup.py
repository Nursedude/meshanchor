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


def test_gateway_kind_peer_skips_http_fetch():
    """Gateway-only peers (e.g. moc3) have no map service on :5000.
    The rollup must include the row so the dashboard shows the box's
    presence, but skip the HTTP fetch — no error chip, no wasted
    round trip. Companion to the renderer-side suppression in
    web/fleet.html and the kind=gateway entry in fleet.json."""
    cfg = _make_config(peers=[
        PeerConfig(name="gw-only", host="gw.example", port=5000, kind="gateway"),
        PeerConfig(name="regular", host="regular.example", port=5001, kind="noc"),
    ])

    calls = []

    def fake_http(url, timeout=3.0):
        calls.append(url)
        return {"host": "regular", "overall_status": "ready"}, None

    with patch("monitoring.fleet_aggregator._http_get_json", side_effect=fake_http):
        rollup = fr.collect_fleet_rollup(cfg)

    by_name = {p.name: p for p in rollup.peers}
    assert set(by_name) == {"gw-only", "regular"}

    gw = by_name["gw-only"]
    assert gw.kind == "gateway"
    assert gw.snapshot is None
    assert gw.error is None
    assert gw.fetch_duration_s == 0.0

    # The regular peer was fetched; the gateway peer was not.
    assert any("regular.example" in c for c in calls)
    assert not any("gw.example" in c for c in calls)


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


# ──────────────────────────────────────────────────────────────────────
# MeshForge co-install pass-through (Track 2.6)
# ──────────────────────────────────────────────────────────────────────


class TestMeshForgeBlockMerge:
    """When MF is co-installed on the same box, MA's self_snapshot
    surfaces MF's observability blocks (path_table, interfaces,
    cascade) — so the operator's existing MA dashboard shows the
    answer to 'where did this go + why did it fail' for this box
    without having to add MF peers to MA's fleet.json."""

    @pytest.fixture(autouse=True)
    def force_mf_co_installed(self, monkeypatch):
        """Pretend MF is installed so the install-check guard added
        2026-05-20 doesn't short-circuit these merge tests. Tests in
        TestMeshForgeBlockMergeInstallCheck exercise the guard itself.
        """
        fr._meshforge_co_installed.cache_clear()
        monkeypatch.setattr(
            fr, "_meshforge_co_installed", lambda: True,
        )

    def test_merge_populates_missing_keys_from_mf_slo(self):
        self_snap = {"host": "self", "uptime_s": 100.0}
        mf_body = {
            "host": "self",
            "path_table": {"available": True, "count": 4, "ts": 1.0},
            "interfaces": {"available": True, "count": 2, "online_count": 2,
                           "ts": 1.0},
            "cascade": {"total": 1, "clean": 1, "suspected": 0,
                        "pre_fail": 0, "wedged": 0, "degraded": 0},
            "ci_status": {"available": True},  # MA owns this key — must NOT clobber
        }
        fr._merge_mesh_forge_blocks(
            self_snap, fetch=lambda url, timeout: (mf_body, None),
        )
        assert self_snap["path_table"] == mf_body["path_table"]
        assert self_snap["interfaces"] == mf_body["interfaces"]
        assert self_snap["cascade"] == mf_body["cascade"]

    def test_merge_never_clobbers_existing_keys(self):
        """MA's slo already populates `host`, `uptime_s`, `ci_status`
        etc. from MA's own collectors. The merge must NEVER overwrite
        a key MA already owns — additive only."""
        ma_ci_status = {"available": True, "source": "ma"}
        self_snap = {"host": "ma", "ci_status": ma_ci_status,
                     "path_table": {"sentinel": "ma_existing"}}
        mf_body = {
            "ci_status": {"available": True, "source": "mf_should_be_ignored"},
            "path_table": {"sentinel": "mf_should_be_ignored"},
            "interfaces": {"available": True, "count": 0, "online_count": 0,
                           "ts": 1.0},
        }
        fr._merge_mesh_forge_blocks(
            self_snap, fetch=lambda url, timeout: (mf_body, None),
        )
        # MA-owned keys unchanged.
        assert self_snap["ci_status"] == ma_ci_status
        assert self_snap["path_table"]["sentinel"] == "ma_existing"
        # New key populated.
        assert self_snap["interfaces"]["count"] == 0

    def test_merge_silent_on_fetch_error(self):
        """MF isn't co-installed — fetch returns (None, error_str). Must
        not raise; self_snapshot stays unmodified."""
        self_snap = {"host": "self"}
        fr._merge_mesh_forge_blocks(
            self_snap, fetch=lambda url, timeout: (None, "connection refused"),
        )
        assert self_snap == {"host": "self"}

    def test_merge_silent_on_fetch_exception(self):
        """Fetch raises (e.g. socket error). Must not propagate."""
        def boom(url, timeout):
            raise OSError("network down")
        self_snap = {"host": "self"}
        fr._merge_mesh_forge_blocks(self_snap, fetch=boom)
        assert self_snap == {"host": "self"}

    def test_merge_silent_on_non_dict_body(self):
        """MF returns something weird (HTML error page parsed as None).
        Must skip silently."""
        self_snap = {"host": "self"}
        fr._merge_mesh_forge_blocks(
            self_snap, fetch=lambda url, timeout: ("not a dict", None),
        )
        assert self_snap == {"host": "self"}

    def test_merge_does_nothing_when_self_snapshot_invalid(self):
        """Defensive: caller passes None or a non-dict — must not raise."""
        fr._merge_mesh_forge_blocks(
            None, fetch=lambda url, timeout: ({}, None),
        )
        fr._merge_mesh_forge_blocks(
            "not a dict", fetch=lambda url, timeout: ({}, None),
        )
        # No assertion needed — would have raised on bug.

    def test_passthrough_blocks_lock_in(self):
        """Lock the set of blocks MA pass-through-merges. Adding a new
        MF block should be a deliberate co-change, not a silent diff."""
        assert fr._MF_PASSTHROUGH_BLOCKS == (
            "path_table", "interfaces", "cascade",
        )

    def test_slo_view_calls_merge(self):
        """End-to-end: slo_view runs the merge so peers polling
        /fleet/slo see the same blocks self does (peer rendering
        depends on this — peer.snapshot IS the peer's slo, not
        their rollup)."""
        from monitoring import fleet_aggregator as fa
        mf_body = {
            "path_table": {"available": True, "count": 7, "ts": 1.0},
        }

        def _fake_fetch(url, timeout):
            if url == fr.MESHFORGE_LOCAL_SLO_URL:
                return mf_body, None
            return None, "down"

        # Minimal FleetSnapshot — slo_view tolerates empty. `boundaries`
        # is dict, not list — `_boundary_top` calls .items().
        snap = fa.FleetSnapshot(
            generated_at=1.0, host="testbox", uptime_s=1.0,
            services={}, radio={}, errors=[],
            chat_total=0, chat_recent=[], boundaries={},
            daemon_health=None,
        )
        with patch("monitoring.fleet_aggregator._http_get_json",
                   side_effect=_fake_fetch):
            slo = fa.slo_view(snap)
        assert slo.get("path_table", {}).get("count") == 7


class TestMeshForgeBlockMergeInstallCheck:
    """Pin the install-check guard added 2026-05-20 after a sustained
    self-recursion storm on meshanchor-server.

    Symptom: ~7 req/s of `GET /fleet/slo` with matching BrokenPipeError
    rate, 415 CPU min in 6 h 13 m wall. Root cause:
    `MESHFORGE_LOCAL_SLO_URL` hardcodes ``localhost:5000``; on an MA-only
    box that's MA itself, so the merge fetched its own /fleet/slo
    response. Each external trigger fanned out via the 0.5 s fetch
    timeout. The function's docstring promised "silent miss if MF
    isn't co-installed" but there was no actual check.
    """

    def setup_method(self):
        # Clear cached install check between tests so each one starts
        # fresh — the lru_cache is module-global.
        fr._meshforge_co_installed.cache_clear()

    def test_skips_fetch_when_mf_not_installed(self, monkeypatch):
        monkeypatch.setattr(
            fr, "_meshforge_co_installed", lambda: False,
        )
        fetch_calls = []
        def fake_fetch(url, timeout):
            fetch_calls.append(url)
            return None, "should not be called"
        self_snap = {"host": "ma-only"}
        fr._merge_mesh_forge_blocks(self_snap, fetch=fake_fetch)
        assert fetch_calls == [], (
            "Install check failed: merge fetched localhost:5000 even "
            "though MF isn't installed. This is the self-recursion "
            "storm class. Got fetches: " + repr(fetch_calls)
        )
        assert self_snap == {"host": "ma-only"}, (
            "self_snap mutated despite skipped merge"
        )

    def test_proceeds_with_fetch_when_mf_installed(self, monkeypatch):
        monkeypatch.setattr(
            fr, "_meshforge_co_installed", lambda: True,
        )
        fetch_calls = []
        def fake_fetch(url, timeout):
            fetch_calls.append(url)
            return ({"path_table": {"count": 3}}, None)
        self_snap = {"host": "co-install"}
        fr._merge_mesh_forge_blocks(self_snap, fetch=fake_fetch)
        assert fetch_calls == [fr.MESHFORGE_LOCAL_SLO_URL]
        assert self_snap.get("path_table", {}).get("count") == 3

    def test_install_check_uses_canonical_path(self):
        """Lock the install-root constant so a future refactor doesn't
        silently point at the wrong directory and let the recursion
        come back."""
        assert str(fr.MESHFORGE_INSTALL_PATH) == "/opt/meshforge"

    def test_install_check_is_cached(self):
        """The check runs on every /fleet/slo handler call — must not
        stat() per request. lru_cache(1) makes it once-per-process."""
        fr._meshforge_co_installed.cache_clear()
        for _ in range(50):
            fr._meshforge_co_installed()
        info = fr._meshforge_co_installed.cache_info()
        assert info.misses == 1, (
            f"Install check ran filesystem stat {info.misses}x in 50 "
            "invocations; lru_cache regression — would re-stat() on "
            "every slo request and dilute the perf win of the guard."
        )
        assert info.hits == 49
