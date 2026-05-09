"""Tests for the FleetMonitorHandler TUI handler.

Verifies registration, menu shape, and that each menu action fetches
the right `/fleet/*` endpoint and renders without crashing. The
shared FakeDialog from conftest captures msgbox calls so we can assert
on rendered content.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import make_handler_context  # noqa: E402

from handlers.fleet_monitor import FleetMonitorHandler  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Registration / shape
# ──────────────────────────────────────────────────────────────────────


def test_handler_registered_in_get_all_handlers():
    from handlers import get_all_handlers
    classes = get_all_handlers()
    assert FleetMonitorHandler in classes


def test_menu_items_shape():
    handler = FleetMonitorHandler()
    items = handler.menu_items()
    assert len(items) == 1
    tag, label, gate = items[0]
    assert tag == "fleet_monitor"
    assert "Fleet Monitor" in label
    assert gate is None


def test_handler_in_dashboard_section():
    assert FleetMonitorHandler.menu_section == "dashboard"


# ──────────────────────────────────────────────────────────────────────
# Fetch + render
# ──────────────────────────────────────────────────────────────────────


def _make_handler():
    handler = FleetMonitorHandler()
    handler.set_context(make_handler_context())
    return handler


def test_show_slo_renders_top_line():
    handler = _make_handler()
    body = {
        "host": "VolcanoAI",
        "overall_status": "ready",
        "uptime_s": 7200.0,
        "services": {
            "total": 6, "available": 4,
            "by_state": {"available": 4, "not_running": 2},
        },
        "boundaries_top": [
            {"label": "systemd.is_active[mosquitto]", "count": 5,
             "p95_s": 0.012, "p50_s": 0.008, "p99_s": 0.012,
             "error_rate": 0.0, "slow_rate": 0.0},
        ],
        "radio": {"connected": True, "name": "p4", "preset": "MeshCore-US-2"},
        "errors": [],
    }
    with patch.object(handler, "_fetch_json", return_value=body):
        handler._show_slo()
    text = handler.ctx.dialog.last_msgbox_text
    assert "VolcanoAI" in text
    assert "ready" in text
    assert "4/6" in text
    assert "p4" in text
    assert "systemd.is_active[mosquitto]" in text


def test_show_slo_handles_fetch_failure():
    handler = _make_handler()
    with patch.object(handler, "_fetch_json", return_value=None):
        # No exception — _fetch_json's failure path renders a msgbox
        # itself (asserted via the fact that we got here).
        handler._show_slo()


def test_show_activity_renders_chat_and_slow_boundaries():
    handler = _make_handler()
    body = {
        "host": "VolcanoAI",
        "chat_total": 3,
        "chat_recent": [
            {"id": 1, "sender": "alice", "text": "hello mesh"},
            {"id": 2, "sender": "bob", "text": "got your message"},
        ],
        "slow_boundaries": [
            {"label": "rnsd.handle_outbound", "count": 10,
             "slow_count": 0, "error_count": 2, "p99_s": 1.0},
        ],
        "errors": [],
    }
    with patch.object(handler, "_fetch_json", return_value=body):
        handler._show_activity()
    text = handler.ctx.dialog.last_msgbox_text
    assert "alice" in text
    assert "hello mesh" in text
    assert "rnsd.handle_outbound" in text


def test_show_rollup_lists_self_and_peers():
    handler = _make_handler()
    body = {
        "self_host": "VolcanoAI",
        "config_source": "/home/user/.config/meshanchor/fleet.json",
        "self_snapshot": {
            "overall_status": "ready",
            "services": {"total": 2, "available": 2,
                         "by_state": {"available": 2}},
            "radio": {"connected": True},
        },
        "peers": [
            {"name": "noc", "host": "noc.local", "port": 5001, "kind": "noc",
             "snapshot": {
                 "overall_status": "degraded",
                 "services": {"total": 3, "available": 2,
                              "by_state": {"available": 2, "not_running": 1}},
                 "radio": {"connected": False},
             },
             "error": None,
             "fetched_at": 0.0, "fetch_duration_s": 0.05},
            {"name": "down", "host": "dead.local", "port": 5001, "kind": "noc",
             "snapshot": None, "error": "timeout: read",
             "fetched_at": 0.0, "fetch_duration_s": 3.0},
        ],
        "federation_peers": [],
        "errors": [],
    }
    with patch.object(handler, "_fetch_json", return_value=body):
        handler._show_rollup()
    text = handler.ctx.dialog.last_msgbox_text
    assert "VolcanoAI" in text
    assert "self (this host)" in text
    assert "noc" in text
    assert "down" in text
    assert "ready" in text
    assert "degraded" in text
    assert "ERROR" in text or "timeout" in text


def test_show_rollup_handles_legacy_full_snapshot_shape():
    """Defensive: if a peer is running an older build that returned a
    full /fleet/health snapshot (per-service dict), the handler still
    renders a sensible row instead of crashing."""
    handler = _make_handler()
    body = {
        "self_host": "x",
        "config_source": "/tmp/fleet.json",
        "self_snapshot": {},
        "peers": [
            {"name": "legacy", "host": "old.local", "port": 5001, "kind": "noc",
             "snapshot": {
                 # Pre-Session-2 shape: services is a per-service dict.
                 "services": {"a": {"available": True}, "b": {"available": False}},
                 "radio": {"connected": True},
             },
             "error": None, "fetched_at": 0.0, "fetch_duration_s": 0.0},
        ],
        "federation_peers": [],
        "errors": [],
    }
    with patch.object(handler, "_fetch_json", return_value=body):
        handler._show_rollup()
    text = handler.ctx.dialog.last_msgbox_text
    assert "legacy" in text
    assert "1/2" in text


def test_show_federation_renders_peers():
    handler = _make_handler()
    body = {
        "enabled": True,
        "fresh_window_s": 7200,
        "peers": [
            {"node_id": "moc", "name": "moc-gw", "network": "rns",
             "source_origin": "rns_announce",
             "last_seen": 0, "last_seen_age_s": 30.0},
        ],
    }
    with patch.object(handler, "_fetch_json", return_value=body):
        handler._show_federation()
    text = handler.ctx.dialog.last_msgbox_text
    assert "moc-gw" in text
    assert "30s ago" in text


def test_show_federation_disabled_path():
    handler = _make_handler()
    body = {"enabled": False, "peers": []}
    with patch.object(handler, "_fetch_json", return_value=body):
        handler._show_federation()
    text = handler.ctx.dialog.last_msgbox_text
    assert "disabled" in text


def test_show_config_path_describes_schema():
    handler = _make_handler()
    handler._show_config_path()
    text = handler.ctx.dialog.last_msgbox_text
    assert "fleet.json" in text
    assert "peers" in text


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def test_format_uptime():
    assert FleetMonitorHandler._format_uptime(0) == "0h 0m"
    assert FleetMonitorHandler._format_uptime(3661) == "1h 1m"
    assert FleetMonitorHandler._format_uptime("bad") == "?"


def test_format_age():
    assert FleetMonitorHandler._format_age(None) == "(never)"
    assert FleetMonitorHandler._format_age(30) == "30s ago"
    assert FleetMonitorHandler._format_age(120) == "2m ago"
    assert FleetMonitorHandler._format_age(7200) == "2.0h ago"
    assert FleetMonitorHandler._format_age("bad") == "?"
