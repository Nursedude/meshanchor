"""Tests for MeshAnchor role-drift detection (utils/role_drift.py) and its
wiring into the fleet watchdog as the ``role_drift`` blackout kind.

MeshCore-side port of MeshForge's probe_role_drift tests (2026-07-18). The
detection module answers the point-in-time "is there drift?" question; the
watchdog owns the 2-cycle hysteresis (same shape as daemon_dead).

Run: python3 -m pytest tests/test_role_drift.py -v
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from utils import role_drift as rd
from monitoring import fleet_watchdog as wd


def _action(verb, item="u", current="x", desired="y", required=True):
    return SimpleNamespace(verb=verb, item=item, current=current,
                           desired=desired, required=required)


# --------------------------------------------------------------------------
# collect_drift_items — filtering logic (plan mocked)
# --------------------------------------------------------------------------

class TestCollectDriftItems:
    def _collect(self, actions, role="meshanchor-noc"):
        # A truthy fake module so _load_provision_role's None-guard passes.
        fake_mod = SimpleNamespace()
        with patch.object(rd, "_load_provision_role", return_value=fake_mod), \
             patch.object(rd, "_plan_role_actions", return_value=actions):
            return rd.collect_drift_items(deployment=(role, {}))

    def test_clean_plan_returns_empty(self):
        assert self._collect([_action("noop"), _action("noop")]) == []

    def test_change_verbs_are_drift(self):
        items = self._collect([_action("enable", item="meshanchor-map")])
        assert len(items) == 1 and "meshanchor-map" in items[0]

    def test_required_warn_is_drift(self):
        items = self._collect([_action("warn", item="rnsd", required=True)])
        assert len(items) == 1 and "rnsd" in items[0]

    def test_advisory_warn_not_drift(self):
        assert self._collect([_action("warn", item="x", required=False)]) == []

    def test_no_role_is_none(self):
        fake_mod = SimpleNamespace()
        with patch.object(rd, "_load_provision_role", return_value=fake_mod):
            assert rd.collect_drift_items(deployment=(None, {})) is None

    def test_tool_unavailable_is_none(self):
        with patch.object(rd, "_load_provision_role", return_value=None):
            assert rd.collect_drift_items(deployment=("meshanchor-noc", {})) is None

    def test_catalog_unavailable_is_none(self):
        fake_mod = SimpleNamespace()
        with patch.object(rd, "_load_provision_role", return_value=fake_mod), \
             patch.object(rd, "_plan_role_actions", return_value=None):
            assert rd.collect_drift_items(deployment=("meshanchor-noc", {})) is None

    def test_unknown_role_is_drift(self):
        fake_mod = SimpleNamespace()
        with patch.object(rd, "_load_provision_role", return_value=fake_mod), \
             patch.object(rd, "_plan_role_actions", side_effect=KeyError("nope")):
            items = rd.collect_drift_items(deployment=("nope", {}))
        assert len(items) == 1 and "not in the fleet_roles.yaml catalog" in items[0]


# --------------------------------------------------------------------------
# evaluate_role_drift — reason string / None
# --------------------------------------------------------------------------

class TestEvaluateRoleDrift:
    def test_clean_is_none(self):
        with patch.object(rd, "collect_drift_items", return_value=[]):
            assert rd.evaluate_role_drift(deployment=("meshanchor-noc", {})) is None

    def test_drift_returns_reason(self):
        with patch.object(rd, "collect_drift_items",
                          return_value=["meshanchor-map: absent -> enabled"]):
            reason = rd.evaluate_role_drift(deployment=("meshanchor-noc", {}))
        assert reason is not None
        assert "meshanchor-noc" in reason and "meshanchor-map" in reason
        assert "1 item(s)" in reason

    def test_indeterminate_is_none(self):
        with patch.object(rd, "collect_drift_items", return_value=None):
            assert rd.evaluate_role_drift(deployment=("meshanchor-noc", {})) is None


# --------------------------------------------------------------------------
# fleet_watchdog wiring — role_drift blackout kind + hysteresis
# --------------------------------------------------------------------------

class TestWatchdogWiring:
    def setup_method(self):
        wd._reset_role_drift_state()

    def teardown_method(self):
        wd._reset_role_drift_state()

    def test_role_drift_in_all_kinds(self):
        assert wd.KIND_ROLE_DRIFT in wd.ALL_KINDS

    def test_hysteresis_requires_two_cycles(self):
        with patch("utils.role_drift.evaluate_role_drift", return_value="DRIFT"):
            first = wd._role_drift_reason()   # streak 1 -> not yet
            second = wd._role_drift_reason()  # streak 2 -> fire
        assert first is None
        assert second is not None and "DRIFT" in second and "confirmed over 2" in second

    def test_clean_cycle_resets_streak(self):
        with patch("utils.role_drift.evaluate_role_drift", return_value="DRIFT"):
            wd._role_drift_reason()  # streak 1
        with patch("utils.role_drift.evaluate_role_drift", return_value=None):
            assert wd._role_drift_reason() is None
        assert wd._role_drift_state["drift_streak"] == 0

    def test_evaluate_exception_is_swallowed(self):
        with patch("utils.role_drift.evaluate_role_drift",
                   side_effect=RuntimeError("boom")):
            assert wd._role_drift_reason() is None

    def test_detect_silence_includes_role_drift_empty_table(self):
        with patch("monitoring.fleet_history.query_latest_heartbeat", return_value=None), \
             patch.object(wd, "_daemon_dead_reason", return_value=None), \
             patch.object(wd, "_role_drift_reason", return_value="RD"):
            out = wd.detect_silence()
        assert out[wd.KIND_ROLE_DRIFT] == "RD"
