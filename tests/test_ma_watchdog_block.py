"""Tests for MA's native /api/status.watchdog block (fleet-truth enrichment).

The block speaks the cross-domain fleet-truth contract in MeshAnchor's OWN
idiom: blackout KINDS are the closed enum, active blackout rows are the
signals, coverage marks every kind per read. Honesty pins: a dead/unknown
organ can never read green; an unobservable state reads DARK downstream, an
inactive unit reads FAILED, a missing unit reads benign-absent.
"""
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from monitoring.fleet_watchdog import ALL_KINDS, KIND_HTTP_DEAD  # noqa: E402
from utils import fleet_truth as ft  # noqa: E402
from utils._map_status_endpoints import watchdog_block_from_blackouts  # noqa: E402
from utils.service_check import ServiceState  # noqa: E402


def _svc(state, available):
    return types.SimpleNamespace(state=state, available=available)


class TestWatchdogBlockFromBlackouts:
    def test_unit_not_installed_is_benign_absent(self):
        with patch("utils.service_check.check_service",
                   return_value=_svc(ServiceState.NOT_INSTALLED, False)):
            b = watchdog_block_from_blackouts()
        assert b["installed"] is False
        c = ft.classify_block(b, source="t")
        assert c["state"] == ft.DARK and c["absent"] is True

    def test_unit_inactive_is_observed_fault(self):
        with patch("utils.service_check.check_service",
                   return_value=_svc(ServiceState.NOT_RUNNING, False)):
            b = watchdog_block_from_blackouts()
        assert b["ok"] is False
        assert ft.classify_block(b, source="t")["state"] == ft.FAILED

    def test_state_check_failure_is_dark_not_fault(self):
        with patch("utils.service_check.check_service",
                   side_effect=RuntimeError("dbus down")):
            b = watchdog_block_from_blackouts()
        assert "unobservable" in b["reason"]
        assert ft.classify_block(b, source="t")["state"] == ft.DARK

    def test_active_organ_no_blackouts_all_kinds_clean(self):
        with patch("utils.service_check.check_service",
                   return_value=_svc(ServiceState.AVAILABLE, True)), \
             patch("monitoring.fleet_history.query_active_blackouts",
                   return_value=[]):
            b = watchdog_block_from_blackouts()
        assert b["ok"] is True
        assert set(b["coverage"].keys()) == set(ALL_KINDS)
        assert all(v["disp"] == "clean" for v in b["coverage"].values())
        assert ft.classify_block(b, source="t")["state"] == ft.HEALTHY
        cov = ft.merge_coverage(b, list(ALL_KINDS))
        assert cov["green"] == len(ALL_KINDS) and cov["red"] == 0

    def test_active_blackout_is_signal_and_red(self):
        row = {"id": 1, "kind": KIND_HTTP_DEAD, "ts_started": 123.0,
               "ts_ended": None, "reason": "heartbeat stale 900s"}
        with patch("utils.service_check.check_service",
                   return_value=_svc(ServiceState.AVAILABLE, True)), \
             patch("monitoring.fleet_history.query_active_blackouts",
                   return_value=[row]):
            b = watchdog_block_from_blackouts()
        assert b["ok"] is False
        assert b["signals"][0]["class"] == KIND_HTTP_DEAD
        assert b["coverage"][KIND_HTTP_DEAD]["disp"] == "active"
        assert ft.classify_block(b, source="t")["state"] == ft.FAILED
        cov = ft.merge_coverage(b, list(ALL_KINDS))
        assert cov["red"] == 1 and cov["green"] == len(ALL_KINDS) - 1

    def test_blackout_store_failure_is_dark(self):
        with patch("utils.service_check.check_service",
                   return_value=_svc(ServiceState.AVAILABLE, True)), \
             patch("monitoring.fleet_history.query_active_blackouts",
                   side_effect=RuntimeError("db locked")):
            b = watchdog_block_from_blackouts()
        assert "unobservable" in b["reason"]
        assert ft.classify_block(b, source="t")["state"] == ft.DARK

    def test_garbage_rows_skipped_not_crash(self):
        with patch("utils.service_check.check_service",
                   return_value=_svc(ServiceState.AVAILABLE, True)), \
             patch("monitoring.fleet_history.query_active_blackouts",
                   return_value=["garbage", {"no": "kind"}]):
            b = watchdog_block_from_blackouts()
        assert b["ok"] is True  # nothing classifiable = no observed fault
        assert all(v["disp"] == "clean" for v in b["coverage"].values())


class TestStatusWiring:
    def test_serve_status_wires_both_blocks(self):
        """Source pin: /api/status emits the watchdog block + the explicit
        mini benign-absence (the live end-to-end is deploy-verified)."""
        text = (_SRC / "utils" / "_map_status_endpoints.py").read_text()
        assert 'status["watchdog"] = watchdog_block_from_blackouts()' in text
        assert '"mini_dudeai"' in text and '"installed": False' in text


class TestCollectorEnum:
    def test_signal_classes_are_the_blackout_kinds(self):
        from utils import fleet_truth_collector as c
        assert c.SIGNAL_CLASSES == list(ALL_KINDS)
