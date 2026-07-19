"""Tests for monitoring.fleet_config — peer config loader.

The "always returns" contract from `fleet_aggregator` extends here:
no matter how broken the on-disk fleet.json is, `load_fleet_config()`
returns a usable `FleetConfig` object. The dashboard never fails to
render because of an editor mishap.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from monitoring.fleet_config import (
    FederationConfig,
    FleetConfig,
    PeerConfig,
    DEFAULT_PEER_PORT,
    get_fleet_config_path,
    load_fleet_config,
)


def test_missing_file_returns_empty_config(tmp_path):
    cfg = load_fleet_config(tmp_path / "nope.json")
    assert isinstance(cfg, FleetConfig)
    assert cfg.peers == []
    assert cfg.parse_error is None
    assert cfg.federation.scrape_rns_announces is True
    # Defaults still populate so the rollup endpoint doesn't 5xx.


def test_loads_well_formed_config(tmp_path):
    p = tmp_path / "fleet.json"
    p.write_text(json.dumps({
        "peers": [
            {"name": "noc", "host": "noc.example", "port": 5001, "kind": "noc"},
            {"name": "dev", "host": "dev.example", "port": 5002, "kind": "dev"},
        ],
        "federation": {"scrape_rns_announces": False, "fresh_window_s": 600},
    }))
    cfg = load_fleet_config(p)
    assert len(cfg.peers) == 2
    assert cfg.peers[0].name == "noc"
    assert cfg.peers[1].port == 5002
    assert cfg.federation.scrape_rns_announces is False
    assert cfg.federation.fresh_window_s == 600


def test_malformed_json_records_parse_error(tmp_path):
    p = tmp_path / "fleet.json"
    p.write_text("{not json")
    cfg = load_fleet_config(p)
    assert cfg.parse_error is not None
    assert "JSONDecodeError" in cfg.parse_error
    # Defaults still apply; rollup endpoint surfaces the error in errors[].
    assert cfg.peers == []


def test_top_level_array_rejected(tmp_path):
    p = tmp_path / "fleet.json"
    p.write_text(json.dumps([{"name": "noc", "host": "h"}]))
    cfg = load_fleet_config(p)
    assert cfg.parse_error == "top-level JSON must be an object"
    assert cfg.peers == []


def test_partial_peer_entries_dropped(tmp_path):
    p = tmp_path / "fleet.json"
    p.write_text(json.dumps({
        "peers": [
            {"name": "valid", "host": "h"},
            {"name": "noport-default-applied", "host": "h2"},
            {"host": "no-name"},          # missing name
            {"name": "no-host"},          # missing host
            "string-not-object",          # malformed entry
            {"name": "bad-port", "host": "h3", "port": "not-int"},
        ],
    }))
    cfg = load_fleet_config(p)
    names = [peer.name for peer in cfg.peers]
    assert names == ["valid", "noport-default-applied", "bad-port"]
    # Defaulted port for the bad-port entry.
    bad = next(p for p in cfg.peers if p.name == "bad-port")
    assert bad.port == DEFAULT_PEER_PORT


def test_non_self_peers_filters_local_hostname(tmp_path, monkeypatch):
    """Exact-name/loopback legs need no resolution; a suffix-differing
    mDNS twin (MyHost.local) is excluded only because it POSITIVELY
    resolves to a local address (2026-07-19 identity-based fix)."""
    from monitoring import fleet_config as fc
    fc._SELF_IP_CACHE.clear()
    monkeypatch.setattr(fc, "_resolve_ips",
                        lambda h: ["127.0.0.1"] if h == "myhost.local" else [])
    monkeypatch.setattr(fc, "_ip_is_local", lambda ip: ip == "127.0.0.1")
    p = tmp_path / "fleet.json"
    p.write_text(json.dumps({
        "peers": [
            {"name": "self-by-name", "host": "MyHost", "port": 5001},
            {"name": "self-by-fqdn", "host": "MyHost.local", "port": 5001},
            {"name": "self-by-loopback", "host": "127.0.0.1", "port": 5002},
            {"name": "remote", "host": "elsewhere.example", "port": 5001},
        ],
    }))
    cfg = load_fleet_config(p)
    remote = cfg.non_self_peers(hostname="MyHost")
    assert [p.name for p in remote] == ["remote"]


def test_non_self_peers_keeps_remote_with_colliding_bare_name(tmp_path, monkeypatch):
    """THE dropped-row fix: a genuinely remote peer whose bare name matches
    this host (noc.remote-site.lan polled from a box named 'noc') must be
    KEPT — the old suffix-stripper silently dropped it."""
    from monitoring import fleet_config as fc
    fc._SELF_IP_CACHE.clear()
    monkeypatch.setattr(fc, "_resolve_ips", lambda h: ["198.51.100.20"])
    monkeypatch.setattr(fc, "_ip_is_local", lambda ip: False)  # remote
    p = tmp_path / "fleet.json"
    p.write_text(json.dumps({
        "peers": [{"name": "site-noc", "host": "noc.remote-site.lan", "port": 5001}],
    }))
    cfg = load_fleet_config(p)
    assert [x.name for x in cfg.non_self_peers(hostname="noc")] == ["site-noc"]


def test_non_self_peers_excludes_ip_written_self_entry(tmp_path, monkeypatch):
    """The double-count fix: a self entry written by IP is excluded once the
    bind test confirms the address is local."""
    from monitoring import fleet_config as fc
    fc._SELF_IP_CACHE.clear()
    monkeypatch.setattr(fc, "_resolve_ips", lambda h: [h])
    monkeypatch.setattr(fc, "_ip_is_local", lambda ip: ip == "192.0.2.10")
    p = tmp_path / "fleet.json"
    p.write_text(json.dumps({
        "peers": [
            {"name": "self-by-ip", "host": "192.0.2.10", "port": 5001},
            {"name": "remote-by-ip", "host": "192.0.2.99", "port": 5001},
        ],
    }))
    cfg = load_fleet_config(p)
    assert [x.name for x in cfg.non_self_peers(hostname="MyHost")] == ["remote-by-ip"]


def test_non_self_peers_resolution_failure_keeps_peer(tmp_path, monkeypatch):
    """Identity unknown ≠ self: when the colliding name cannot be resolved,
    the peer is KEPT — a true-self kept is a visible duplicate; a
    true-remote dropped vanishes silently. Fail-visible wins."""
    from monitoring import fleet_config as fc
    fc._SELF_IP_CACHE.clear()
    monkeypatch.setattr(fc, "_resolve_ips", lambda h: [])  # DNS dead
    p = tmp_path / "fleet.json"
    p.write_text(json.dumps({
        "peers": [{"name": "maybe-twin", "host": "noc.other.lan", "port": 5001}],
    }))
    cfg = load_fleet_config(p)
    assert [x.name for x in cfg.non_self_peers(hostname="noc")] == ["maybe-twin"]


def test_ip_is_local_bind_probe_real():
    """The real primitive: loopback binds (local), TEST-NET cannot."""
    from monitoring import fleet_config as fc
    assert fc._ip_is_local("127.0.0.1") is True
    assert fc._ip_is_local("203.0.113.1") is False


def test_env_override_redirects_path(tmp_path, monkeypatch):
    target = tmp_path / "alt.json"
    target.write_text(json.dumps({"peers": [{"name": "n", "host": "h"}]}))
    monkeypatch.setenv("MESHANCHOR_FLEET_CONFIG", str(target))
    assert get_fleet_config_path() == target
    cfg = load_fleet_config()
    assert len(cfg.peers) == 1


def test_default_path_uses_real_user_home(monkeypatch):
    """Path resolution must honor MF001 — under sudo, fall back to the
    real user's home, never /root."""
    monkeypatch.delenv("MESHANCHOR_FLEET_CONFIG", raising=False)
    fake_home = Path("/home/testuser")
    with patch("utils.paths.get_real_user_home", return_value=fake_home):
        p = get_fleet_config_path()
    assert p == fake_home / ".config" / "meshanchor" / "fleet.json"


def test_peer_base_url_format():
    peer = PeerConfig(name="x", host="example", port=5001)
    assert peer.base_url() == "http://example:5001"
