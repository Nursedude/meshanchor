"""Tests for utils.fleet_api — daemon-side `/fleet/federation` endpoint.

Issue #102: the daemon's RNS service registry is the only source for
federation peer freshness; the map's directory cache doesn't see RNS
announces. These tests cover the `_build_payload` shape and the
`handle_get` HTTP delegate, including the 503 fall-through that lets
the map mixin choose to use its directory-filter fallback instead.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from utils import fleet_api


# ──────────────────────────────────────────────────────────────────────
# _build_payload
# ──────────────────────────────────────────────────────────────────────


def _make_registry_with(snapshot):
    registry = MagicMock()
    registry.get_all_with_timing.return_value = snapshot
    registry.get_stats.return_value = {"LXMF_DELIVERY": len(snapshot)}
    return registry


def _make_info(name, service_type_name="LXMF_DELIVERY", aspect="lxmf.delivery"):
    info = MagicMock()
    info.service_type = MagicMock()
    info.service_type.name = service_type_name
    info.aspect = aspect
    info.display_name = name
    info.latitude = None
    info.longitude = None
    info.battery = None
    return info


def test_build_payload_shapes_peer_rows():
    now_dt = datetime(2026, 5, 9, 10, 0, 0)
    last_dt = now_dt - timedelta(seconds=30)
    first_dt = now_dt - timedelta(hours=1)
    snapshot = {
        "aa" * 16: {
            "info": _make_info("moc"),
            "first": first_dt,
            "last": last_dt,
        },
    }
    registry = _make_registry_with(snapshot)
    with patch("gateway.rns_services.get_service_registry", return_value=registry):
        payload = fleet_api._build_payload(now_unix=now_dt.timestamp())
    assert len(payload["peers"]) == 1
    peer = payload["peers"][0]
    assert peer["hash_hex"] == "aa" * 16
    assert peer["name"] == "moc"
    assert peer["service_type"] == "LXMF_DELIVERY"
    assert peer["aspect"] == "lxmf.delivery"
    assert peer["last_seen_age_s"] == pytest.approx(30, abs=0.5)
    assert peer["first_seen_age_s"] == pytest.approx(3600, abs=1.0)
    assert payload["stats"] == {"LXMF_DELIVERY": 1}


def test_build_payload_sorts_newest_first():
    now_dt = datetime(2026, 5, 9, 10, 0, 0)
    snapshot = {
        "aa" * 16: {
            "info": _make_info("old"),
            "first": now_dt - timedelta(hours=2),
            "last": now_dt - timedelta(hours=1),
        },
        "bb" * 16: {
            "info": _make_info("new"),
            "first": now_dt - timedelta(seconds=60),
            "last": now_dt - timedelta(seconds=10),
        },
        "cc" * 16: {
            "info": _make_info("mid"),
            "first": now_dt - timedelta(minutes=30),
            "last": now_dt - timedelta(minutes=5),
        },
    }
    registry = _make_registry_with(snapshot)
    with patch("gateway.rns_services.get_service_registry", return_value=registry):
        payload = fleet_api._build_payload(now_unix=now_dt.timestamp())
    names = [p["name"] for p in payload["peers"]]
    assert names == ["new", "mid", "old"]


def test_build_payload_handles_empty_registry():
    registry = _make_registry_with({})
    registry.get_stats.return_value = {}
    with patch("gateway.rns_services.get_service_registry", return_value=registry):
        payload = fleet_api._build_payload(now_unix=1788127200.0)
    assert payload["peers"] == []
    assert payload["stats"] == {}


# ──────────────────────────────────────────────────────────────────────
# handle_get HTTP delegate
# ──────────────────────────────────────────────────────────────────────


def _make_http_handler():
    h = MagicMock()
    h._send_json = MagicMock()
    h._send_error_json = MagicMock()
    return h


def test_handle_get_emits_payload_when_registry_loads():
    snapshot = {
        "aa" * 16: {
            "info": _make_info("moc"),
            "first": datetime(2026, 5, 9, 9, 0, 0),
            "last": datetime(2026, 5, 9, 10, 0, 0),
        },
    }
    registry = _make_registry_with(snapshot)
    handler = _make_http_handler()
    with patch("gateway.rns_services.get_service_registry", return_value=registry):
        fleet_api.handle_get(handler)
    handler._send_json.assert_called_once()
    body = handler._send_json.call_args[0][0]
    assert "peers" in body
    assert body["peers"][0]["name"] == "moc"
    handler._send_error_json.assert_not_called()


def test_handle_get_returns_503_when_rns_module_missing():
    handler = _make_http_handler()
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "gateway.rns_services":
            raise ImportError("rns_services not available")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        fleet_api.handle_get(handler)
    handler._send_error_json.assert_called_once()
    args, _ = handler._send_error_json.call_args
    assert args[0] == 503
    handler._send_json.assert_not_called()


def test_handle_get_returns_500_on_registry_exception():
    registry = MagicMock()
    registry.get_all_with_timing.side_effect = RuntimeError("registry broken")
    handler = _make_http_handler()
    with patch("gateway.rns_services.get_service_registry", return_value=registry):
        fleet_api.handle_get(handler)
    handler._send_error_json.assert_called_once()
    assert handler._send_error_json.call_args[0][0] == 500


# ──────────────────────────────────────────────────────────────────────
# _datetime_to_unix robustness
# ──────────────────────────────────────────────────────────────────────


def test_datetime_to_unix_handles_datetime():
    dt = datetime(2026, 5, 9, 0, 0, 0)
    assert fleet_api._datetime_to_unix(dt) == pytest.approx(dt.timestamp())


def test_datetime_to_unix_handles_unix_float():
    assert fleet_api._datetime_to_unix(1788127200.5) == 1788127200.5


def test_datetime_to_unix_handles_none():
    assert fleet_api._datetime_to_unix(None) == 0.0
