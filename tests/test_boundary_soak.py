"""Tests for scripts/boundary_soak.py — regex parsing, diffing, report writing."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# scripts/ is not on the import path by default; add it once.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import boundary_soak as bs  # noqa: E402


class TestParseJournal:
    def test_parses_slow_line(self):
        line = (
            "May 06 12:00:00 host meshanchor-gateway[123]: WARNING boundary "
            "rpc[meshtasticd.toradio_put[a1b2c3d4]] slow: 4.231s "
            "(>=2.0s threshold)"
        )
        result = bs.parse_journal(line)
        assert "meshtasticd.toradio_put[a1b2c3d4]" in result
        rec = result["meshtasticd.toradio_put[a1b2c3d4]"]
        assert rec["slow"] == 1
        assert rec["raised"] == 0
        assert rec["max_s"] == pytest.approx(4.231)
        assert rec["sum_s"] == pytest.approx(4.231)

    def test_parses_raised_line(self):
        line = "rpc[rnsd.has_path] raised after 2.118s"
        result = bs.parse_journal(line)
        assert result["rnsd.has_path"]["raised"] == 1
        assert result["rnsd.has_path"]["slow"] == 0
        assert result["rnsd.has_path"]["max_s"] == pytest.approx(2.118)

    def test_label_without_target(self):
        line = "rpc[meshtasticd.cli_info] slow: 12.5s (>=10.0s threshold)"
        result = bs.parse_journal(line)
        assert "meshtasticd.cli_info" in result
        assert result["meshtasticd.cli_info"]["slow"] == 1

    def test_aggregates_multiple_lines(self):
        text = "\n".join([
            "rpc[a.b] slow: 3.0s (>=2.0s threshold)",
            "rpc[a.b] slow: 5.0s (>=2.0s threshold)",
            "rpc[a.b] raised after 1.0s",
        ])
        rec = bs.parse_journal(text)["a.b"]
        assert rec["slow"] == 2
        assert rec["raised"] == 1
        assert rec["max_s"] == pytest.approx(5.0)
        assert rec["sum_s"] == pytest.approx(9.0)

    def test_separate_targets_are_separate_labels(self):
        text = "\n".join([
            "rpc[meshtasticd.send_text[!abc12345]] slow: 3.0s (>=2.0s threshold)",
            "rpc[meshtasticd.send_text[!def67890]] slow: 2.5s (>=2.0s threshold)",
        ])
        result = bs.parse_journal(text)
        assert "meshtasticd.send_text[!abc12345]" in result
        assert "meshtasticd.send_text[!def67890]" in result
        assert result["meshtasticd.send_text[!abc12345]"]["slow"] == 1
        assert result["meshtasticd.send_text[!def67890]"]["slow"] == 1

    def test_ignores_unrelated_lines(self):
        text = (
            "INFO whatever\n"
            "DEBUG some other thing\n"
            "rpc[ok.line] ok 0.034s\n"  # DEBUG ok lines should NOT count
        )
        # 'ok' is neither 'slow' nor 'raised' — regex must skip
        assert bs.parse_journal(text) == {}

    def test_empty_input(self):
        assert bs.parse_journal("") == {}


class TestDiffAgainstPrior:
    def test_new_label_is_flagged(self):
        current = {
            "x.y": {"slow": 3, "raised": 0, "max_s": 4.0, "sum_s": 12.0}
        }
        deltas = bs.diff_against_prior(current, prior=None)
        assert deltas["x.y"]["is_new"] is True
        assert deltas["x.y"]["d_slow"] == 3
        assert deltas["x.y"]["d_raised"] == 0

    def test_existing_label_diff(self):
        current = {"x.y": {"slow": 5, "raised": 1, "max_s": 4.0, "sum_s": 12.0}}
        prior = {
            "labels": {
                "x.y": {"slow": 3, "raised": 0, "max_s": 2.0, "sum_s": 6.0},
            }
        }
        deltas = bs.diff_against_prior(current, prior=prior)
        assert deltas["x.y"]["is_new"] is False
        assert deltas["x.y"]["d_slow"] == 2
        assert deltas["x.y"]["d_raised"] == 1

    def test_negative_delta_when_count_drops(self):
        # Counts can drop if a label was hot one window and quiet the next.
        current = {"x.y": {"slow": 1, "raised": 0, "max_s": 2.0, "sum_s": 2.0}}
        prior = {
            "labels": {
                "x.y": {"slow": 5, "raised": 2, "max_s": 4.0, "sum_s": 12.0},
            }
        }
        deltas = bs.diff_against_prior(current, prior=prior)
        assert deltas["x.y"]["d_slow"] == -4
        assert deltas["x.y"]["d_raised"] == -2

    def test_prior_with_no_labels_key(self):
        # Defensive: tolerate a malformed prior payload.
        current = {"x.y": {"slow": 1, "raised": 0, "max_s": 2.0, "sum_s": 2.0}}
        deltas = bs.diff_against_prior(current, prior={"foo": "bar"})
        assert deltas["x.y"]["is_new"] is True


class TestWriteReports:
    def test_writes_md_and_json_sidecar(self, tmp_path):
        ts = datetime(2026, 5, 5, 18, 30, 0, tzinfo=timezone.utc)
        current = {"a.b": {"slow": 1, "raised": 0, "max_s": 2.5, "sum_s": 2.5}}
        deltas = {"a.b": {"d_slow": 1, "d_raised": 0, "is_new": True}}
        path = bs.write_reports(
            tmp_path, ts, "6 hours ago", ["unit-a"], current, deltas,
        )
        assert path.exists()
        assert path.suffix == ".md"
        body = path.read_text()
        assert "Boundary soak" in body
        assert "`a.b`" in body
        assert "+1" in body  # delta column

        json_path = path.with_suffix(".json")
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["labels"]["a.b"]["slow"] == 1
        assert data["window"] == "6 hours ago"
        assert data["units"] == ["unit-a"]

    def test_empty_current_writes_clean_message(self, tmp_path):
        ts = datetime.now(timezone.utc)
        path = bs.write_reports(tmp_path, ts, "6 hours ago", ["u"], {}, {})
        body = path.read_text()
        assert "No `rpc[*]` WARN lines" in body
        # JSON sidecar should still be written for the next-run diff
        json_path = path.with_suffix(".json")
        assert json_path.exists()
        assert json.loads(json_path.read_text())["labels"] == {}

    def test_creates_out_dir_if_missing(self, tmp_path):
        nested = tmp_path / "nested" / "soak_reports"
        ts = datetime.now(timezone.utc)
        path = bs.write_reports(nested, ts, "6h", ["u"], {}, {})
        assert path.exists()
        assert nested.is_dir()

    def test_orders_by_max_descending(self, tmp_path):
        ts = datetime.now(timezone.utc)
        current = {
            "fast": {"slow": 1, "raised": 0, "max_s": 2.1, "sum_s": 2.1},
            "slow": {"slow": 1, "raised": 0, "max_s": 9.0, "sum_s": 9.0},
            "mid": {"slow": 1, "raised": 0, "max_s": 4.0, "sum_s": 4.0},
        }
        deltas = {
            label: {"d_slow": 0, "d_raised": 0, "is_new": False}
            for label in current
        }
        path = bs.write_reports(tmp_path, ts, "6h", ["u"], current, deltas)
        body = path.read_text()
        slow_idx = body.index("`slow`")
        mid_idx = body.index("`mid`")
        fast_idx = body.index("`fast`")
        assert slow_idx < mid_idx < fast_idx


class TestCheckLiveness:
    def _now(self) -> datetime:
        return datetime(2026, 5, 5, 18, 0, 0, tzinfo=timezone.utc)

    def test_returns_none_when_no_prior(self):
        # First run ever — no prior to compare against, no warning.
        assert bs.check_liveness(None, self._now(), max_gap_secs=9 * 3600) is None

    def test_returns_none_when_within_window(self):
        prior_ts = (self._now() - timedelta(hours=4)).isoformat()
        assert bs.check_liveness(
            {"timestamp": prior_ts}, self._now(), max_gap_secs=9 * 3600,
        ) is None

    def test_flags_when_gap_exceeds_threshold(self):
        prior_ts = (self._now() - timedelta(hours=12)).isoformat()
        age = bs.check_liveness(
            {"timestamp": prior_ts}, self._now(), max_gap_secs=9 * 3600,
        )
        assert age is not None
        assert age == pytest.approx(12 * 3600, rel=1e-3)

    def test_returns_none_when_timestamp_missing(self):
        # Defensive: malformed prior payload.
        assert bs.check_liveness(
            {"window": "6h"}, self._now(), max_gap_secs=9 * 3600,
        ) is None

    def test_returns_none_when_timestamp_unparseable(self):
        assert bs.check_liveness(
            {"timestamp": "not-an-iso-date"}, self._now(),
            max_gap_secs=9 * 3600,
        ) is None

    def test_handles_naive_timestamp(self):
        # If the prior was written without tzinfo (older format), assume UTC
        # rather than crashing on a TypeError comparing aware vs naive.
        prior_ts = (self._now() - timedelta(hours=12)).replace(tzinfo=None).isoformat()
        age = bs.check_liveness(
            {"timestamp": prior_ts}, self._now(), max_gap_secs=9 * 3600,
        )
        assert age is not None
        assert age == pytest.approx(12 * 3600, rel=1e-3)


class TestFindPriorData:
    def test_returns_none_when_dir_missing(self, tmp_path):
        assert bs.find_prior_data(tmp_path / "nope") is None

    def test_returns_none_when_dir_empty(self, tmp_path):
        assert bs.find_prior_data(tmp_path) is None

    def test_returns_most_recent_by_filename(self, tmp_path):
        (tmp_path / "20260101T000000Z.json").write_text(
            json.dumps({"labels": {"old.thing": {"slow": 1}}})
        )
        (tmp_path / "20260102T000000Z.json").write_text(
            json.dumps({"labels": {"new.thing": {"slow": 2}}})
        )
        prior = bs.find_prior_data(tmp_path)
        assert prior is not None
        assert "new.thing" in prior["labels"]
        assert "old.thing" not in prior["labels"]

    def test_tolerates_corrupt_json(self, tmp_path):
        (tmp_path / "20260102T000000Z.json").write_text("not valid json {")
        assert bs.find_prior_data(tmp_path) is None
