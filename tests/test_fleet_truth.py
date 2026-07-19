"""Tests for the honest fleet-truth SSOT (utils/fleet_truth.py) — MA copy.

The builder is BYTE-IDENTICAL with MeshForge's (enforced by the lead repo's
parity_check.py); these tests pin the same contract on this side of the twin:
NO missing / stale / absent / indeterminate input may ever produce a
``healthy`` cell — "no data" can never read green. Plus the fleet-verdict
worst-of roll-up and the default-dark coverage map.

MeshAnchor has no SIGNAL_CLASSES closed enum (its watchdog speaks
blackout-rows-by-kind), so the coverage tests here use literal class lists —
the MF twin additionally pins its full enum.

Run: python3 -m pytest tests/test_fleet_truth.py -v
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils import fleet_truth as ft  # noqa: E402

NOW = 1_000_000.0


# ── cell / worst_of primitives ──────────────────────────────────────────
class TestCellPrimitives:
    def test_invalid_state_raises(self):
        # a bug must not silently become a healthy-looking cell
        with pytest.raises(ValueError):
            ft.cell("green")

    def test_worst_of_precedence(self):
        assert ft.worst_of([ft.HEALTHY, ft.DARK, ft.FAILED]) == ft.FAILED
        assert ft.worst_of([ft.HEALTHY, ft.DARK]) == ft.DARK
        assert ft.worst_of([ft.HEALTHY]) == ft.HEALTHY

    def test_worst_of_empty_is_dark(self):
        # observed nothing => cannot claim health
        assert ft.worst_of([]) == ft.DARK


# ── classify_block: the default-dark heart ──────────────────────────────
class TestClassifyBlock:
    def test_none_block_is_dark(self):
        assert ft.classify_block(None, source="x")["state"] == ft.DARK

    def test_not_installed_is_dark(self):
        c = ft.classify_block({"installed": False}, source="x")
        assert c["state"] == ft.DARK

    def test_stale_reason_is_dark_not_healthy(self):
        # frozen producer serving old-but-ok JSON must read DARK
        c = ft.classify_block(
            {"installed": True, "ok": False,
             "reason": "stale: last write 900s ago"}, source="x")
        assert c["state"] == ft.DARK

    def test_ok_true_is_the_only_green(self):
        c = ft.classify_block({"installed": True, "ok": True, "ts": NOW, "age_s": 5},
                              source="x")
        assert c["state"] == ft.HEALTHY

    def test_ok_false_real_fault_is_failed(self):
        c = ft.classify_block(
            {"installed": True, "ok": False, "reason": "wedge signals: rns"},
            source="x")
        assert c["state"] == ft.FAILED

    @pytest.mark.parametrize("block", [
        None, {}, {"installed": False}, {"installed": True},
        {"installed": True, "ok": False, "reason": "stale"},
        {"installed": True, "reason": "read_error: boom"},
    ])
    def test_no_bad_input_yields_healthy(self, block):
        """THE money invariant — nothing but an explicit ok:True is healthy."""
        assert ft.classify_block(block, source="x")["state"] != ft.HEALTHY


# ── coverage map ─────────────────────────────────────────────────────────
class TestCoverage:
    def test_empty_enum_is_honest_zero(self):
        """MA passes an empty class list today — totals must be 0, never
        invented coverage."""
        wd = {"installed": True, "ok": True, "signals": []}
        cov = ft.merge_coverage(wd, [])
        assert cov["total"] == 0
        assert cov["green"] == cov["red"] == cov["dark"] == 0

    def test_unknown_is_dark_not_green(self):
        wd = {"installed": True, "ok": True, "signals": []}
        cov = ft.merge_coverage(wd, ["blackout_kind_a", "blackout_kind_b"])
        assert cov["green"] == 0
        assert cov["dark"] == 2

    def test_reported_clean_is_green_and_reason_rides(self):
        wd = {"installed": True, "ok": True, "signals": [],
              "coverage": {"a": {"disp": "clean"},
                           "b": {"disp": "inert", "reason": "organ absent by role"}}}
        cov = ft.merge_coverage(wd, ["a", "b"])
        assert cov["green"] == 1 and cov["dark"] == 1
        assert cov["classes"]["b"]["reason"] == "organ absent by role"

    def test_unobservable_watchdog_all_dark(self):
        cov = ft.merge_coverage({"installed": False}, ["a", "b", "c"])
        assert cov["dark"] == 3 and cov["green"] == 0


# ── build_box_truth ──────────────────────────────────────────────────────
class TestBoxTruth:
    def _snap(self, **kw):
        base = {"alias": "peer-noc", "resolution_method": "config",
                "status": None, "slo": None, "error": None, "answered_at": NOW}
        base.update(kw)
        return base

    def test_unreachable_box_is_dark(self):
        b = ft.build_box_truth(self._snap(status=None, slo=None, error="timeout"),
                               now=NOW, signal_classes=[])
        assert b["reachable"]["state"] == ft.DARK
        assert "timeout" in (b["reachable"]["reason"] or "")

    def test_healthy_box(self):
        snap = self._snap(
            status={"app": {"name": "meshanchor", "role": "noc"},
                    "watchdog": {"installed": True, "ok": True, "signals": []},
                    "mini_dudeai": {"installed": True, "ok": True}},
            slo={"overall_status": "ready", "cascade": {"pre_fail": 0, "wedged": 0},
                 "ci_status": {"repos": [{"name": "ma", "state": "success"}]},
                 "radio": {"connected": True}, "schedules": {}, "path_table": {}})
        b = ft.build_box_truth(snap, now=NOW, signal_classes=[])
        assert b["reachable"]["state"] == ft.HEALTHY
        assert b["subsystems"]["watchdog"]["state"] == ft.HEALTHY
        assert b["subsystems"]["services"]["state"] == ft.HEALTHY
        assert b["reachable"]["resolution_method"] == "config"

    def test_ma_status_without_watchdog_blocks_reads_dark_not_green(self):
        """MA's /api/status carries no watchdog/mini blocks today — those
        subsystem cells must be DARK, never inferred healthy."""
        snap = self._snap(status={"app": {"name": "meshanchor"}},
                          slo={"overall_status": "ready"})
        b = ft.build_box_truth(snap, now=NOW, signal_classes=[])
        assert b["subsystems"]["watchdog"]["state"] == ft.DARK
        assert b["subsystems"]["mini"]["state"] == ft.DARK
        assert b["subsystems"]["services"]["state"] == ft.HEALTHY

    def test_failed_service_is_failed(self):
        snap = self._snap(status={"app": {}},
                          slo={"overall_status": "degraded"})
        b = ft.build_box_truth(snap, now=NOW, signal_classes=[])
        assert b["subsystems"]["services"]["state"] == ft.FAILED


# ── build_fleet_truth: verdict + fan-out honesty ────────────────────────
class TestFleetTruth:
    def _healthy_snap(self, alias):
        return {"alias": alias, "resolution_method": "config", "answered_at": NOW,
                "status": {"app": {"name": "meshanchor"},
                           "watchdog": {"installed": True, "ok": True},
                           "mini_dudeai": {"installed": True, "ok": True}},
                "slo": {"overall_status": "ready", "cascade": {"pre_fail": 0, "wedged": 0},
                        "ci_status": {"repos": [{"name": "ma", "state": "success"}]},
                        "radio": {"connected": True}, "schedules": {}, "path_table": {}}}

    def test_all_healthy_verdict_healthy(self):
        snaps = [self._healthy_snap("noc-a"), self._healthy_snap("noc-b")]
        t = ft.build_fleet_truth(snaps, now=NOW, signal_classes=[], noc_host="noc-a")
        assert t["fleet_state"] == ft.HEALTHY
        assert t["counts"]["healthy"] == 2
        assert t["fanout"]["stale"] is False

    def test_incomplete_fanout_forces_non_green(self):
        snaps = [self._healthy_snap("noc-a")]  # 1 answered
        t = ft.build_fleet_truth(snaps, now=NOW, signal_classes=[],
                                 noc_host="noc-a", hosts_declared=3)
        assert t["fanout"]["stale"] is True
        assert t["fleet_state"] != ft.HEALTHY  # dark fan-out can't read green

    def test_dark_box_taints_verdict(self):
        dark = {"alias": "peer-x", "resolution_method": "config",
                "status": None, "slo": None, "error": "no route", "answered_at": None}
        t = ft.build_fleet_truth([self._healthy_snap("noc-a"), dark],
                                 now=NOW, signal_classes=[], noc_host="noc-a")
        assert t["counts"]["dark"] == 1
        assert t["fleet_state"] != ft.HEALTHY

    def test_failed_box_makes_verdict_failed(self):
        bad = self._healthy_snap("noc-b")
        bad["slo"]["overall_status"] = "degraded"
        t = ft.build_fleet_truth([self._healthy_snap("noc-a"), bad],
                                 now=NOW, signal_classes=[], noc_host="noc-a")
        assert t["fleet_state"] == ft.FAILED

    def test_empty_fleet_is_dark_not_healthy(self):
        t = ft.build_fleet_truth([], now=NOW, signal_classes=[], noc_host="noc-a",
                                 hosts_declared=2)
        assert t["fleet_state"] != ft.HEALTHY

    def test_schema_tag(self):
        t = ft.build_fleet_truth([], now=NOW, signal_classes=[], noc_host="noc-a")
        assert t["schema"] == "fleet_truth/v1"
