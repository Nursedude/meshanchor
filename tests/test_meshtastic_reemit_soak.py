"""Tests for scripts/meshtastic_reemit_soak.py — parsing, diffing, reports."""
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

import meshtastic_reemit_soak as rs  # noqa: E402


# Real-shape sample lines, copied from the bridge source so a future
# rename in meshtastic_reemit_bridge.py fails this test loudly rather
# than silently quieting the scraper.
REEMIT_LINE = (
    "INFO meshtastic_reemit: re-emitted on MeshCore ch1 from 32962f10 "
    "(orig ch2 sender !32962f10)"
)
DROP_ACK_LINE = (
    "DEBUG meshtastic_reemit: dropping synth-ACK shaped content from "
    "32962f10: '[delivered:abc] ...'"
)
DROP_NESTED_LINE = (
    "DEBUG meshtastic_reemit: dropping nested-bridge content from "
    "32962f10 (prefix '[MeshCore]'): '[MeshCore] p3 hi'"
)
HANDLER_LINE = (
    "DEBUG meshtastic_reemit: no active MeshCore handler; "
    "dropping re-emit for 32962f10"
)
STARTED_LINE = (
    "INFO meshtastic_reemit: started — 1 source identity(ies), "
    "target channel 1, output '[Mesh:{sender}] {text}'"
)
ERROR_LINE = (
    "ERROR meshtastic_reemit: unexpected error: ConnectionResetError(54)"
)
WARN_INVALID_PATTERN_LINE = (
    "WARNING meshtastic_reemit: invalid strip_pattern '(' (unbalanced "
    "parenthesis); falling back to default"
)
WARN_BAD_FORMAT_LINE = (
    "WARNING meshtastic_reemit: bad output_format '{nope}' (KeyError) — "
    "using fallback"
)
WARN_EMPTY_ALLOWLIST_LINE = (
    "WARNING meshtastic_reemit: enabled but source_identities is empty "
    "— bridge will receive nothing"
)


class TestParseJournal:
    def test_parses_reemit_line(self):
        result = rs.parse_journal(REEMIT_LINE)
        assert result["reemitted_total"] == 1
        assert result["by_source"] == {"32962f10": 1}
        assert result["by_target_channel"] == {"1": 1}

    def test_parses_synth_ack_drop(self):
        result = rs.parse_journal(DROP_ACK_LINE)
        assert result["filtered_synth_ack"] == 1
        assert result["reemitted_total"] == 0

    def test_parses_nested_bridge_drop(self):
        result = rs.parse_journal(DROP_NESTED_LINE)
        assert result["filtered_nested_bridge"] == 1
        # nested-bridge drops are distinct from synth-ack drops
        assert result["filtered_synth_ack"] == 0

    def test_parses_handler_unavailable(self):
        result = rs.parse_journal(HANDLER_LINE)
        assert result["handler_unavailable"] == 1

    def test_parses_restart(self):
        result = rs.parse_journal(STARTED_LINE)
        assert result["restarts"] == 1

    def test_parses_error(self):
        result = rs.parse_journal(ERROR_LINE)
        assert result["errors"] == 1

    def test_parses_warning_invalid_pattern(self):
        assert rs.parse_journal(WARN_INVALID_PATTERN_LINE)["warnings"] == 1

    def test_parses_warning_bad_format(self):
        assert rs.parse_journal(WARN_BAD_FORMAT_LINE)["warnings"] == 1

    def test_parses_warning_empty_allowlist(self):
        assert rs.parse_journal(WARN_EMPTY_ALLOWLIST_LINE)["warnings"] == 1

    def test_aggregates_multiple_sources_and_channels(self):
        text = "\n".join([
            "INFO meshtastic_reemit: re-emitted on MeshCore ch1 from "
            "32962f10 (orig ch2 sender !32962f10)",
            "INFO meshtastic_reemit: re-emitted on MeshCore ch1 from "
            "aaa2365f (orig ch2 sender !aaa2365f)",
            "INFO meshtastic_reemit: re-emitted on MeshCore ch0 from "
            "32962f10 (orig ch2 sender !32962f10)",
        ])
        result = rs.parse_journal(text)
        assert result["reemitted_total"] == 3
        assert result["by_source"] == {"32962f10": 2, "aaa2365f": 1}
        assert result["by_target_channel"] == {"1": 2, "0": 1}

    def test_mixed_traffic_aggregates_correctly(self):
        text = "\n".join([
            STARTED_LINE,
            REEMIT_LINE,
            REEMIT_LINE,
            DROP_ACK_LINE,
            DROP_NESTED_LINE,
            HANDLER_LINE,
            ERROR_LINE,
            WARN_BAD_FORMAT_LINE,
        ])
        result = rs.parse_journal(text)
        assert result["reemitted_total"] == 2
        assert result["filtered_synth_ack"] == 1
        assert result["filtered_nested_bridge"] == 1
        assert result["handler_unavailable"] == 1
        assert result["errors"] == 1
        assert result["warnings"] == 1
        assert result["restarts"] == 1

    def test_ignores_unrelated_lines(self):
        text = (
            "INFO something else entirely\n"
            "DEBUG another module talking\n"
            "INFO meshtastic_reemit: stopped\n"  # not a tracked event
        )
        result = rs.parse_journal(text)
        # 'stopped' isn't a counter we aggregate (only restarts matter
        # for regression detection)
        assert result["reemitted_total"] == 0
        assert result["restarts"] == 0
        assert result["errors"] == 0

    def test_empty_input(self):
        result = rs.parse_journal("")
        assert result["reemitted_total"] == 0
        assert result["by_source"] == {}
        assert result["by_target_channel"] == {}


class TestDiffAgainstPrior:
    def test_no_prior_returns_current_as_delta(self):
        current = rs.parse_journal(REEMIT_LINE)
        deltas = rs.diff_against_prior(current, prior=None)
        assert deltas["reemitted_total"] == 1
        assert deltas["new_sources"] == ["32962f10"]
        assert deltas["new_channels"] == ["1"]

    def test_existing_counter_increments(self):
        current = {
            "reemitted_total": 10,
            "filtered_synth_ack": 2,
            "filtered_nested_bridge": 0,
            "handler_unavailable": 1,
            "errors": 0,
            "warnings": 0,
            "restarts": 1,
            "by_source": {"32962f10": 10},
            "by_target_channel": {"1": 10},
        }
        prior = {
            "stats": {
                "reemitted_total": 6,
                "filtered_synth_ack": 1,
                "filtered_nested_bridge": 0,
                "handler_unavailable": 0,
                "errors": 0,
                "warnings": 0,
                "restarts": 0,
                "by_source": {"32962f10": 6},
                "by_target_channel": {"1": 6},
            }
        }
        deltas = rs.diff_against_prior(current, prior=prior)
        assert deltas["reemitted_total"] == 4
        assert deltas["filtered_synth_ack"] == 1
        assert deltas["handler_unavailable"] == 1
        assert deltas["restarts"] == 1
        assert deltas["new_sources"] == []
        assert deltas["new_channels"] == []

    def test_new_source_flagged(self):
        current = {
            "reemitted_total": 3,
            "filtered_synth_ack": 0,
            "filtered_nested_bridge": 0,
            "handler_unavailable": 0,
            "errors": 0,
            "warnings": 0,
            "restarts": 0,
            "by_source": {"32962f10": 2, "newhash1": 1},
            "by_target_channel": {"1": 3},
        }
        prior = {
            "stats": {
                "reemitted_total": 2,
                "by_source": {"32962f10": 2},
                "by_target_channel": {"1": 2},
            }
        }
        deltas = rs.diff_against_prior(current, prior=prior)
        assert deltas["new_sources"] == ["newhash1"]

    def test_new_target_channel_flagged(self):
        current = {
            "reemitted_total": 2,
            "filtered_synth_ack": 0,
            "filtered_nested_bridge": 0,
            "handler_unavailable": 0,
            "errors": 0,
            "warnings": 0,
            "restarts": 0,
            "by_source": {"32962f10": 2},
            "by_target_channel": {"1": 1, "0": 1},
        }
        prior = {
            "stats": {
                "reemitted_total": 1,
                "by_source": {"32962f10": 1},
                "by_target_channel": {"1": 1},
            }
        }
        deltas = rs.diff_against_prior(current, prior=prior)
        assert deltas["new_channels"] == ["0"]

    def test_prior_without_stats_key(self):
        # Defensive: malformed prior payload.
        current = rs.parse_journal(REEMIT_LINE)
        deltas = rs.diff_against_prior(current, prior={"foo": "bar"})
        assert deltas["reemitted_total"] == 1
        assert "32962f10" in deltas["new_sources"]


class TestWriteReports:
    def test_writes_md_and_json_sidecar(self, tmp_path):
        ts = datetime(2026, 5, 19, 1, 30, 0, tzinfo=timezone.utc)
        current = rs.parse_journal(
            REEMIT_LINE + "\n" + DROP_ACK_LINE + "\n" + STARTED_LINE
        )
        deltas = rs.diff_against_prior(current, prior=None)
        path = rs.write_reports(
            tmp_path, ts, "6 hours ago", ["meshanchor-daemon"], current, deltas,
        )
        assert path.exists()
        body = path.read_text()
        assert "Meshtastic re-emit soak" in body
        assert "`reemitted_total`" in body
        assert "32962f10" in body
        # by_target_channel section
        assert "ch1" in body

        json_path = path.with_suffix(".json")
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["stats"]["reemitted_total"] == 1
        assert data["window"] == "6 hours ago"
        assert data["units"] == ["meshanchor-daemon"]

    def test_empty_window_writes_quiet_message(self, tmp_path):
        ts = datetime.now(timezone.utc)
        current = rs.parse_journal("")
        deltas = rs.diff_against_prior(current, prior=None)
        path = rs.write_reports(tmp_path, ts, "6h", ["u"], current, deltas)
        body = path.read_text()
        assert "No `meshtastic_reemit:`" in body
        # JSON sidecar still written so next-run diff has something to read.
        json_path = path.with_suffix(".json")
        assert json_path.exists()
        assert json.loads(json_path.read_text())["stats"]["reemitted_total"] == 0

    def test_creates_out_dir_if_missing(self, tmp_path):
        nested = tmp_path / "nested" / "reemit_reports"
        ts = datetime.now(timezone.utc)
        current = rs.parse_journal("")
        deltas = rs.diff_against_prior(current, prior=None)
        path = rs.write_reports(nested, ts, "6h", ["u"], current, deltas)
        assert path.exists()
        assert nested.is_dir()

    def test_delta_column_renders_sign(self, tmp_path):
        ts = datetime.now(timezone.utc)
        current = rs.parse_journal(REEMIT_LINE)
        prior = {"stats": {"reemitted_total": 0}}
        deltas = rs.diff_against_prior(current, prior=prior)
        path = rs.write_reports(tmp_path, ts, "6h", ["u"], current, deltas)
        body = path.read_text()
        assert "+1" in body  # explicit-sign delta column

    def test_new_sources_section_appears_when_present(self, tmp_path):
        ts = datetime.now(timezone.utc)
        current = rs.parse_journal(REEMIT_LINE)
        deltas = rs.diff_against_prior(current, prior=None)
        path = rs.write_reports(tmp_path, ts, "6h", ["u"], current, deltas)
        body = path.read_text()
        assert "New sources this window" in body
        assert "32962f10" in body


class TestCheckLiveness:
    def _now(self) -> datetime:
        return datetime(2026, 5, 19, 1, 0, 0, tzinfo=timezone.utc)

    def test_returns_none_when_no_prior(self):
        assert rs.check_liveness(None, self._now(), max_gap_secs=9 * 3600) is None

    def test_returns_none_when_within_window(self):
        prior_ts = (self._now() - timedelta(hours=4)).isoformat()
        assert rs.check_liveness(
            {"timestamp": prior_ts}, self._now(), max_gap_secs=9 * 3600,
        ) is None

    def test_flags_when_gap_exceeds_threshold(self):
        prior_ts = (self._now() - timedelta(hours=12)).isoformat()
        age = rs.check_liveness(
            {"timestamp": prior_ts}, self._now(), max_gap_secs=9 * 3600,
        )
        assert age is not None
        assert age == pytest.approx(12 * 3600, rel=1e-3)

    def test_returns_none_when_timestamp_missing(self):
        assert rs.check_liveness(
            {"window": "6h"}, self._now(), max_gap_secs=9 * 3600,
        ) is None

    def test_returns_none_when_timestamp_unparseable(self):
        assert rs.check_liveness(
            {"timestamp": "not-an-iso-date"}, self._now(), max_gap_secs=9 * 3600,
        ) is None

    def test_handles_naive_timestamp(self):
        prior_ts = (
            (self._now() - timedelta(hours=12))
            .replace(tzinfo=None).isoformat()
        )
        age = rs.check_liveness(
            {"timestamp": prior_ts}, self._now(), max_gap_secs=9 * 3600,
        )
        assert age is not None
        assert age == pytest.approx(12 * 3600, rel=1e-3)


class TestFindPriorData:
    def test_returns_none_when_dir_missing(self, tmp_path):
        assert rs.find_prior_data(tmp_path / "nope") is None

    def test_returns_none_when_dir_empty(self, tmp_path):
        assert rs.find_prior_data(tmp_path) is None

    def test_returns_most_recent_by_filename(self, tmp_path):
        (tmp_path / "20260101T000000Z.json").write_text(
            json.dumps({"stats": {"reemitted_total": 1}})
        )
        (tmp_path / "20260102T000000Z.json").write_text(
            json.dumps({"stats": {"reemitted_total": 5}})
        )
        prior = rs.find_prior_data(tmp_path)
        assert prior is not None
        assert prior["stats"]["reemitted_total"] == 5

    def test_tolerates_corrupt_json(self, tmp_path):
        (tmp_path / "20260102T000000Z.json").write_text("not valid json {")
        assert rs.find_prior_data(tmp_path) is None
