"""Tests for scripts/boundary_soak_fleet.py — aggregation + report writing.

Rsync logic is shell-shape only and not exercised here; the unit-of-test
is everything the python code actually decides.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import boundary_soak_fleet as bsf  # noqa: E402


def _record(slow: int = 0, raised: int = 0, max_s: float = 0.0) -> dict:
    return {"slow": slow, "raised": raised, "max_s": max_s, "sum_s": max_s}


def _host_payload(labels: dict, window: str = "6 hours ago") -> dict:
    return {"timestamp": "2026-05-05T00:00:00+00:00", "window": window,
            "units": ["meshanchor-gateway"], "labels": labels}


class TestAggregate:
    def test_single_host_passthrough(self):
        per_host = {
            "h1": _host_payload({
                "rnsd.has_path": _record(slow=2, max_s=3.0),
            }),
        }
        rollup = bsf.aggregate(per_host)
        assert rollup["rnsd.has_path"]["total_slow"] == 2
        assert rollup["rnsd.has_path"]["total_raised"] == 0
        assert rollup["rnsd.has_path"]["max_s"] == pytest.approx(3.0)
        assert rollup["rnsd.has_path"]["hosts"] == ["h1"]

    def test_sums_across_hosts(self):
        per_host = {
            "h1": _host_payload({"x.y": _record(slow=2, raised=1, max_s=3.0)}),
            "h2": _host_payload({"x.y": _record(slow=5, raised=0, max_s=4.5)}),
        }
        rollup = bsf.aggregate(per_host)
        assert rollup["x.y"]["total_slow"] == 7
        assert rollup["x.y"]["total_raised"] == 1
        assert rollup["x.y"]["max_s"] == pytest.approx(4.5)
        assert sorted(rollup["x.y"]["hosts"]) == ["h1", "h2"]

    def test_label_only_on_one_host(self):
        per_host = {
            "h1": _host_payload({"only.here": _record(slow=1, max_s=2.5)}),
            "h2": _host_payload({"different": _record(slow=3, max_s=5.0)}),
        }
        rollup = bsf.aggregate(per_host)
        assert rollup["only.here"]["hosts"] == ["h1"]
        assert rollup["different"]["hosts"] == ["h2"]
        assert rollup["only.here"]["total_slow"] == 1
        assert rollup["different"]["total_slow"] == 3

    def test_unreachable_host_skipped(self):
        per_host = {
            "h1": _host_payload({"x.y": _record(slow=2, max_s=3.0)}),
            "h2": None,  # rsync failed
        }
        rollup = bsf.aggregate(per_host)
        assert rollup["x.y"]["hosts"] == ["h1"]
        assert rollup["x.y"]["total_slow"] == 2

    def test_empty_per_host_yields_empty_rollup(self):
        assert bsf.aggregate({}) == {}

    def test_all_hosts_unreachable(self):
        assert bsf.aggregate({"h1": None, "h2": None}) == {}


class TestLatestReportForHost:
    def test_missing_dir(self, tmp_path):
        assert bsf.latest_report_for_host(tmp_path / "absent") is None

    def test_empty_dir(self, tmp_path):
        assert bsf.latest_report_for_host(tmp_path) is None

    def test_picks_most_recent(self, tmp_path):
        (tmp_path / "20260101T000000Z.json").write_text(
            json.dumps(_host_payload({"old": _record(slow=1, max_s=1.0)}))
        )
        (tmp_path / "20260102T000000Z.json").write_text(
            json.dumps(_host_payload({"new": _record(slow=2, max_s=2.0)}))
        )
        result = bsf.latest_report_for_host(tmp_path)
        assert result is not None
        assert "new" in result["labels"]
        assert "old" not in result["labels"]

    def test_corrupt_json_skipped(self, tmp_path):
        (tmp_path / "20260101T000000Z.json").write_text("not json")
        assert bsf.latest_report_for_host(tmp_path) is None


class TestWriteFleetReport:
    def test_writes_md_and_json(self, tmp_path):
        ts = datetime(2026, 5, 5, 18, 30, 0, tzinfo=timezone.utc)
        per_host = {
            "local": _host_payload({"x.y": _record(slow=1, max_s=2.5)}),
            "pi-r1": _host_payload({"x.y": _record(slow=3, max_s=4.0)}),
        }
        rollup = bsf.aggregate(per_host)
        path = bsf.write_fleet_report(tmp_path, ts, per_host, rollup)
        assert path.exists()
        body = path.read_text()
        assert "Fleet soak" in body
        assert "`local`" in body
        assert "`pi-r1`" in body
        assert "`x.y`" in body
        # rollup row carries both hosts
        assert "`local`, `pi-r1`" in body or "`pi-r1`, `local`" in body

        json_path = path.with_suffix(".json")
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["rollup"]["x.y"]["total_slow"] == 4

    def test_unreachable_host_shown_as_dash(self, tmp_path):
        ts = datetime.now(timezone.utc)
        per_host = {
            "local": _host_payload({"x.y": _record(slow=1, max_s=2.0)}),
            "pi-down": None,
        }
        rollup = bsf.aggregate(per_host)
        path = bsf.write_fleet_report(tmp_path, ts, per_host, rollup)
        body = path.read_text()
        assert "`pi-down` | – | – | 0" in body

    def test_no_rollup_when_all_quiet(self, tmp_path):
        ts = datetime.now(timezone.utc)
        per_host = {
            "local": _host_payload({}),
            "pi-r1": _host_payload({}),
        }
        rollup = bsf.aggregate(per_host)
        path = bsf.write_fleet_report(tmp_path, ts, per_host, rollup)
        body = path.read_text()
        assert "stayed under threshold" in body
        assert "## Fleet rollup" not in body

    def test_no_hosts_at_all(self, tmp_path):
        ts = datetime.now(timezone.utc)
        path = bsf.write_fleet_report(tmp_path, ts, {}, {})
        body = path.read_text()
        assert "No hosts configured" in body

    def test_orders_rollup_by_max_latency(self, tmp_path):
        ts = datetime.now(timezone.utc)
        per_host = {
            "local": _host_payload({
                "fast": _record(slow=1, max_s=2.1),
                "slow": _record(slow=1, max_s=9.0),
                "mid": _record(slow=1, max_s=4.0),
            }),
        }
        rollup = bsf.aggregate(per_host)
        path = bsf.write_fleet_report(tmp_path, ts, per_host, rollup)
        body = path.read_text()
        slow_idx = body.index("`slow`")
        mid_idx = body.index("`mid`")
        fast_idx = body.index("`fast`")
        assert slow_idx < mid_idx < fast_idx

    def test_creates_out_dir_if_missing(self, tmp_path):
        ts = datetime.now(timezone.utc)
        nested = tmp_path / "nested" / "fleet"
        path = bsf.write_fleet_report(nested, ts, {}, {})
        assert path.exists()
        assert nested.is_dir()
