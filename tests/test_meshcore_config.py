"""Tests for utils.meshcore_config (Session 3 of MeshCore charter).

Covers preset table lookups, DesiredConfig parsing from gateway.json,
state cache round-trip, drift detection, apply_desired_config orchestration,
and the meshcore_config_doctor diagnostics.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from utils.meshcore_config import (
    DesiredChannel,
    DesiredConfig,
    DoctorIssue,
    DriftReport,
    PRESETS,
    apply_desired_config,
    cache_radio_state,
    check_drift,
    known_presets,
    known_regions,
    load_cached_radio_state,
    lookup_preset,
    meshcore_config_doctor,
    preset_name_for,
)


# ── Preset table ─────────────────────────────────────────────────────


class TestPresetTable:
    def test_lookup_preset_returns_tuple_for_known_pair(self):
        result = lookup_preset("US915", "default_lf")
        assert result == (915.0, 250.0, 11, 5)

    def test_lookup_preset_case_insensitive(self):
        assert lookup_preset("us915", "DEFAULT_LF") == (915.0, 250.0, 11, 5)

    def test_lookup_preset_unknown_returns_none(self):
        assert lookup_preset("XX999", "fake") is None

    def test_lookup_preset_empty_returns_none(self):
        assert lookup_preset("", "") is None

    def test_known_regions_sorted(self):
        regions = known_regions()
        assert regions == sorted(regions)
        assert "US915" in regions
        assert "EU868" in regions

    def test_known_presets_for_region(self):
        us_presets = known_presets("US915")
        assert "default_lf" in us_presets
        assert "medium_fast" in us_presets

    def test_known_presets_unknown_region(self):
        assert known_presets("XX999") == []

    def test_preset_name_for_known(self):
        assert preset_name_for(915.0, 250.0, 11, 5) == ("US915", "default_lf")

    def test_preset_name_for_unknown(self):
        assert preset_name_for(900.0, 125.0, 7, 5) is None

    def test_preset_name_for_partial(self):
        assert preset_name_for(None, 250.0, 11, 5) is None
        assert preset_name_for(915.0, 250.0, None, 5) is None


# ── DesiredConfig parsing ────────────────────────────────────────────


class _FakeMCConfig:
    """Stand-in for MeshCoreConfig with the Session 3 fields."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestDesiredConfig:
    def test_empty_gateway_config_yields_empty_desired(self):
        d = DesiredConfig.from_gateway_config(_FakeMCConfig())
        assert d.is_empty()
        assert not d.has_lora()

    def test_region_preset_expand_to_lora_tuple(self):
        d = DesiredConfig.from_gateway_config(_FakeMCConfig(
            region="US915", preset="default_lf",
        ))
        assert d.has_lora()
        assert d.freq_mhz == 915.0
        assert d.bw_khz == 250.0
        assert d.sf == 11
        assert d.cr == 5

    def test_explicit_overrides_take_precedence(self):
        d = DesiredConfig.from_gateway_config(_FakeMCConfig(
            region="US915", preset="default_lf",
            desired_freq_mhz=914.5,
        ))
        assert d.freq_mhz == 914.5
        # Other fields filled from preset
        assert d.bw_khz == 250.0

    def test_unknown_preset_logs_warning_keeps_overrides(self):
        d = DesiredConfig.from_gateway_config(_FakeMCConfig(
            region="US915", preset="bogus",
            desired_freq_mhz=903.0, desired_bw_khz=125.0,
            desired_sf=9, desired_cr=5,
        ))
        # Falls back to explicit overrides
        assert d.freq_mhz == 903.0
        assert d.has_lora()

    def test_channels_parsed(self):
        d = DesiredConfig.from_gateway_config(_FakeMCConfig(
            desired_channels=[
                {"idx": 0, "name": "#public"},
                {"idx": 1, "name": "ops", "secret": "00" * 16},
            ],
        ))
        assert len(d.channels) == 2
        assert d.channels[0] == DesiredChannel(idx=0, name="#public", secret=None)
        assert d.channels[1].secret == "00" * 16

    def test_bad_channel_entry_skipped(self):
        d = DesiredConfig.from_gateway_config(_FakeMCConfig(
            desired_channels=[
                {"idx": "not-an-int", "name": "ops"},
                {"idx": 0, "name": "#public"},
            ],
        ))
        # Only the well-formed entry survives
        assert len(d.channels) == 1
        assert d.channels[0].idx == 0

    def test_tx_power_parsed(self):
        d = DesiredConfig.from_gateway_config(_FakeMCConfig(
            desired_tx_power_dbm=22,
        ))
        assert d.tx_power_dbm == 22
        assert not d.is_empty()

    def test_string_numbers_coerced(self):
        d = DesiredConfig.from_gateway_config(_FakeMCConfig(
            desired_freq_mhz="915.0", desired_sf="11",
        ))
        assert d.freq_mhz == 915.0
        assert d.sf == 11


# ── State cache ──────────────────────────────────────────────────────


class TestStateCache:
    def test_round_trip(self, tmp_path: Path):
        state = {
            "radio_freq_mhz": 915.0,
            "radio_bw_khz": 250.0,
            "radio_sf": 11,
            "radio_cr": 5,
            "tx_power_dbm": 17,
        }
        path = cache_radio_state(state, config_dir=tmp_path)
        assert path is not None
        assert path.exists()

        loaded = load_cached_radio_state(config_dir=tmp_path)
        assert loaded is not None
        assert loaded["state"]["radio_freq_mhz"] == 915.0
        assert loaded["saved_ts"] > 0

    def test_load_missing_returns_none(self, tmp_path: Path):
        assert load_cached_radio_state(config_dir=tmp_path) is None

    def test_load_corrupt_returns_none(self, tmp_path: Path):
        target = tmp_path / "meshcore_state.json"
        target.write_text("{not valid json")
        assert load_cached_radio_state(config_dir=tmp_path) is None

    def test_secret_field_redacted(self, tmp_path: Path):
        state = {"radio_freq_mhz": 915.0, "secret": "00" * 16}
        cache_radio_state(state, config_dir=tmp_path)
        loaded = load_cached_radio_state(config_dir=tmp_path)
        assert "secret" not in loaded["state"]
        assert loaded["state"]["radio_freq_mhz"] == 915.0


# ── Drift detection ──────────────────────────────────────────────────


class TestCheckDrift:
    def test_no_drift_when_actual_matches_desired(self):
        actual = {
            "radio_freq_mhz": 915.0,
            "radio_bw_khz": 250.0,
            "radio_sf": 11,
            "radio_cr": 5,
            "tx_power_dbm": 17,
        }
        desired = DesiredConfig(
            region="US915", preset="default_lf",
            freq_mhz=915.0, bw_khz=250.0, sf=11, cr=5, tx_power_dbm=17,
        )
        assert check_drift(actual, desired=desired) == []

    def test_drift_on_freq(self):
        actual = {"radio_freq_mhz": 914.0, "radio_bw_khz": 250.0,
                  "radio_sf": 11, "radio_cr": 5}
        desired = DesiredConfig(
            freq_mhz=915.0, bw_khz=250.0, sf=11, cr=5,
        )
        reports = check_drift(actual, desired=desired)
        assert len(reports) == 1
        assert reports[0].field == "radio_freq_mhz"
        assert reports[0].expected == 915.0
        assert reports[0].actual == 914.0

    def test_drift_freq_within_epsilon_no_report(self):
        # Floats within 0.01 MHz match (radio rounding)
        actual = {"radio_freq_mhz": 915.005, "radio_bw_khz": 250.0,
                  "radio_sf": 11, "radio_cr": 5}
        desired = DesiredConfig(freq_mhz=915.0, bw_khz=250.0, sf=11, cr=5)
        reports = check_drift(actual, desired=desired)
        # Filter out other-field drift to isolate freq behavior
        freq_reports = [r for r in reports if r.field == "radio_freq_mhz"]
        assert freq_reports == []

    def test_tx_power_drift(self):
        actual = {"tx_power_dbm": 14}
        desired = DesiredConfig(tx_power_dbm=22)
        reports = check_drift(actual, desired=desired)
        assert len(reports) == 1
        assert reports[0].field == "tx_power_dbm"

    def test_cached_drift(self):
        cached = {"state": {"radio_freq_mhz": 869.525}, "saved_ts": time.time()}
        actual = {"radio_freq_mhz": 915.0}
        reports = check_drift(actual, cached=cached)
        assert len(reports) == 1
        assert reports[0].field == "radio_freq_mhz"
        assert reports[0].severity == "info"

    def test_actual_none_field_skipped(self):
        actual = {"radio_freq_mhz": None}
        desired = DesiredConfig(freq_mhz=915.0, bw_khz=250.0, sf=11, cr=5)
        reports = check_drift(actual, desired=desired)
        # Don't crash, don't report drift on None
        freq_reports = [r for r in reports if r.field == "radio_freq_mhz"]
        assert freq_reports == []


# ── apply_desired_config ─────────────────────────────────────────────


class _FakeHandler:
    """Mock handler that records writes and returns synthetic state."""
    def __init__(self, *, raise_on=None, post_state=None):
        self._raise_on = raise_on or set()
        self._post_state = post_state or {}
        self.calls = []

    def set_radio_lora(self, freq_mhz, bw_khz, sf, cr):
        self.calls.append(("set_radio_lora",
                           dict(freq_mhz=freq_mhz, bw_khz=bw_khz, sf=sf, cr=cr)))
        if "lora" in self._raise_on:
            raise RuntimeError("simulated NAK")
        return self._post_state

    def set_radio_tx_power(self, dbm):
        self.calls.append(("set_radio_tx_power", dict(dbm=dbm)))
        if "tx" in self._raise_on:
            raise RuntimeError("simulated TX cap")
        return self._post_state

    def set_radio_channel(self, idx, name, secret_hex=None):
        self.calls.append(("set_radio_channel",
                           dict(idx=idx, name=name, secret_hex=secret_hex)))
        if "channel" in self._raise_on:
            raise RuntimeError("simulated bad slot")
        return self._post_state

    def get_radio_state(self, refresh=False):
        return self._post_state


class TestApplyDesiredConfig:
    def test_empty_desired_skips(self):
        handler = _FakeHandler()
        result = apply_desired_config(handler, DesiredConfig())
        assert result["applied"] is False
        assert "no desired" in (result["reason"] or "")
        assert handler.calls == []

    def test_handler_without_setters_skips(self):
        # Supervisor-handler-shaped object: no set_radio_* methods
        bare_handler = MagicMock(spec=[])
        result = apply_desired_config(
            bare_handler,
            DesiredConfig(freq_mhz=915.0, bw_khz=250.0, sf=11, cr=5),
        )
        assert result["applied"] is False
        assert "supervisor" in (result["reason"] or "").lower() or \
               "setters" in (result["reason"] or "").lower()

    def test_full_apply_writes_in_order(self):
        post = {
            "radio_freq_mhz": 915.0, "radio_bw_khz": 250.0,
            "radio_sf": 11, "radio_cr": 5,
            "tx_power_dbm": 17, "channels": [],
        }
        handler = _FakeHandler(post_state=post)
        desired = DesiredConfig(
            freq_mhz=915.0, bw_khz=250.0, sf=11, cr=5,
            tx_power_dbm=17,
            channels=[DesiredChannel(idx=0, name="#public")],
        )
        result = apply_desired_config(handler, desired)
        assert result["applied"] is True
        ops = [c[0] for c in handler.calls]
        # LoRa first, then TX, then channels
        assert ops == ["set_radio_lora", "set_radio_tx_power", "set_radio_channel"]
        assert result["drift_after"] == []

    def test_lora_failure_skips_tx(self):
        handler = _FakeHandler(raise_on={"lora"}, post_state={})
        desired = DesiredConfig(
            freq_mhz=915.0, bw_khz=250.0, sf=11, cr=5,
            tx_power_dbm=17,
        )
        result = apply_desired_config(handler, desired)
        ops = [c[0] for c in handler.calls]
        # set_radio_lora raised; TX should not be attempted
        assert "set_radio_tx_power" not in ops
        assert result["applied"] is False
        assert any(e["op"] == "set_radio_lora" for e in result["errors"])

    def test_channel_failure_doesnt_block_others(self):
        post = {"channels": []}
        handler = _FakeHandler(raise_on={"channel"}, post_state=post)
        desired = DesiredConfig(
            channels=[
                DesiredChannel(idx=0, name="#public"),
                DesiredChannel(idx=1, name="ops", secret="00" * 16),
            ],
        )
        result = apply_desired_config(handler, desired)
        # Both channel writes attempted (failure isolated)
        ch_calls = [c for c in handler.calls if c[0] == "set_radio_channel"]
        assert len(ch_calls) == 2
        assert result["applied"] is False
        assert len(result["errors"]) == 2


# ── meshcore_config_doctor ───────────────────────────────────────────


class TestDoctor:
    def test_no_state_no_handler_returns_info(self, tmp_path: Path):
        issues = meshcore_config_doctor(config_dir=tmp_path)
        assert any(i.code == "no_radio_state" for i in issues)
        assert all(isinstance(i, DoctorIssue) for i in issues)

    def test_handler_read_failure_logged(self, tmp_path: Path):
        bad = MagicMock()
        bad.get_radio_state.side_effect = RuntimeError("device gone")
        issues = meshcore_config_doctor(handler=bad, config_dir=tmp_path)
        assert any(i.code == "radio_state_read_failed" for i in issues)

    def test_drift_surfaced(self, tmp_path: Path):
        actual = {"radio_freq_mhz": 914.0, "radio_bw_khz": 250.0,
                  "radio_sf": 11, "radio_cr": 5,
                  "fw_build": "v1.6.0"}
        handler = MagicMock()
        handler.get_radio_state.return_value = actual
        desired = DesiredConfig(freq_mhz=915.0, bw_khz=250.0, sf=11, cr=5)
        issues = meshcore_config_doctor(
            handler=handler, desired=desired, config_dir=tmp_path,
        )
        assert any(i.code.startswith("desired_drift:radio_freq_mhz") for i in issues)
        # Firmware version always reported when present
        assert any(i.code == "firmware_version" for i in issues)

    def test_region_mismatch_error(self, tmp_path: Path):
        actual = {"radio_freq_mhz": 869.525, "radio_bw_khz": 250.0,
                  "radio_sf": 11, "radio_cr": 5}
        handler = MagicMock()
        handler.get_radio_state.return_value = actual
        # Operator says US915 but radio is on EU868 — error.
        desired = DesiredConfig(region="US915", preset="default_lf",
                                freq_mhz=869.525, bw_khz=250.0, sf=11, cr=5)
        issues = meshcore_config_doctor(
            handler=handler, desired=desired, config_dir=tmp_path,
        )
        # At least one error-level issue, and includes region_mismatch
        assert any(i.code == "region_mismatch" and i.severity == "error"
                   for i in issues)

    def test_uses_cached_when_no_handler(self, tmp_path: Path):
        cache_radio_state(
            {"radio_freq_mhz": 915.0, "radio_bw_khz": 250.0,
             "radio_sf": 11, "radio_cr": 5, "fw_build": "v1.5.0"},
            config_dir=tmp_path,
        )
        issues = meshcore_config_doctor(config_dir=tmp_path)
        # Firmware reported from cached snapshot
        assert any(i.code == "firmware_version" for i in issues)
        # Should NOT report no_radio_state since cache is present
        assert not any(i.code == "no_radio_state" for i in issues)
