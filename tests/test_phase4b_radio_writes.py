"""Phase 4b — MeshCore radio write path.

Four layers under test:

1. Module-level validation, region table, secret derivation.
2. MeshCoreRadioConfig setter wrappers (set_lora / set_tx_power /
   set_channel) — input validation, meshcore_py command dispatch, cache
   invalidation after a successful write.
3. ConfigAPIHandler PUT /radio/* dispatch — routing, body parsing,
   error mapping (RadioWriteError -> 400, other -> 500, unknown -> 404).
4. TUI handler write methods — double-confirm path can't be skipped, the
   region warning surfaces in the confirm dialog text.
"""
import asyncio
import io
import json
import os
import sys
import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from utils.config_api import ConfigAPIHandler
from gateway.meshcore_handler import (
    MeshCoreHandler,
    MeshCoreSimulator,
    _clear_active_handler,
    get_active_handler,
)
from gateway.meshcore_radio_config import (
    CHANNEL_NAME_MAX_LEN,
    LORA_BW_KHZ_VALID,
    REGION_BANDS,
    RadioWriteError,
    derive_channel_secret,
    parse_channel_secret,
    region_for_freq,
    validate_channel_name,
    validate_lora_params,
    validate_tx_power,
)
from launcher_tui.handlers.meshcore import MeshCoreHandler as TUIMeshCoreHandler


# ─────────────────────────────────────────────────────────────────────
# Shared fixtures (mirror test_phase4a_radio_readonly.py)
# ─────────────────────────────────────────────────────────────────────


def _make_api_stub(path, method="PUT", body=b""):
    h = ConfigAPIHandler.__new__(ConfigAPIHandler)
    h.path = path
    h.command = method
    h.headers = {
        "Content-Length": str(len(body)),
        "Content-Type": "application/json",
    }
    h.rfile = io.BytesIO(body)
    h.wfile = io.BytesIO()
    h.client_address = ("127.0.0.1", 50000)
    h.api = None
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    return h


def _read_response(handler) -> dict:
    raw = handler.wfile.getvalue().decode()
    return json.loads(raw) if raw else {}


@pytest.fixture
def mock_meshcore_config():
    meshcore = SimpleNamespace(
        enabled=True, device_path="/dev/ttyUSB1", baud_rate=115200,
        connection_type="serial", tcp_host="localhost", tcp_port=4000,
        auto_fetch_messages=True, bridge_channels=True, bridge_dms=True,
        simulation_mode=True, channel_poll_interval_sec=5,
    )
    return SimpleNamespace(
        meshcore=meshcore,
        meshtastic=SimpleNamespace(host="localhost", port=4403),
    )


@pytest.fixture
def daemon_handler(mock_meshcore_config):
    h = MeshCoreHandler(
        config=mock_meshcore_config,
        node_tracker=MagicMock(),
        health=MagicMock(record_error=MagicMock(return_value="transient")),
        stop_event=threading.Event(),
        stats={},
        stats_lock=threading.Lock(),
        message_queue=Queue(maxsize=10),
    )
    yield h
    _clear_active_handler(h)


# Fake meshcore_py event-type enum for the cases where we patch
# _HAS_MESHCORE True without installing the real module.
class _FakeEventType:
    OK = "ok"
    ERROR = "error"
    SELF_INFO = "self_info"
    DEVICE_INFO = "device_info"
    CHANNEL_INFO = "channel_info"


def _ok_event():
    return SimpleNamespace(type=_FakeEventType.OK, payload={})


def _patch_meshcore_module():
    """Patch the optional dep flags so a MagicMock meshcore is acceptable."""
    return [
        patch("gateway.meshcore_handler._HAS_MESHCORE", True),
        patch(
            "gateway.meshcore_handler._meshcore_mod",
            SimpleNamespace(EventType=_FakeEventType),
        ),
    ]


# ─────────────────────────────────────────────────────────────────────
# 1. Module-level validation
# ─────────────────────────────────────────────────────────────────────


class TestRegionTable:
    def test_region_for_433_is_eu433(self):
        b = region_for_freq(433.5)
        assert b is not None and b.label == "EU433"

    def test_region_for_869_is_eu868(self):
        b = region_for_freq(869.525)
        assert b is not None and b.label == "EU868"

    def test_region_for_915_is_us915(self):
        b = region_for_freq(915.0)
        assert b is not None and b.label == "US915"

    def test_region_overlap_picks_narrowest(self):
        # 921 MHz lies in both KR920 (920.9-923.3) and US915 (902-928).
        # KR920 is narrower so it should win.
        b = region_for_freq(921.0)
        assert b is not None and b.label == "KR920"

    def test_region_for_unknown_returns_none(self):
        assert region_for_freq(2400.0) is None
        assert region_for_freq(None) is None

    def test_table_caps_match_published_sources(self):
        labels = {b.label: b for b in REGION_BANDS}
        # ETSI EN 300 220 — 25 mW ERP = 14 dBm
        assert labels["EU868"].max_tx_dbm == 14
        # FCC Part 15.247(b)(3) — 1 W = 30 dBm
        assert labels["US915"].max_tx_dbm == 30
        # ETSI EN 300 220 — 10 mW ERP = 10 dBm
        assert labels["EU433"].max_tx_dbm == 10
        # KCC — 25 mW EIRP = 14 dBm
        assert labels["KR920"].max_tx_dbm == 14


class TestLoRaValidation:
    def test_eu_default_accepted(self):
        validate_lora_params(869.525, 250.0, 11, 5)

    def test_us_default_accepted(self):
        validate_lora_params(915.0, 250.0, 11, 5)

    def test_freq_outside_pll_range_rejected(self):
        with pytest.raises(RadioWriteError, match="PLL"):
            validate_lora_params(2400.0, 250.0, 11, 5)
        with pytest.raises(RadioWriteError, match="PLL"):
            validate_lora_params(50.0, 250.0, 11, 5)

    def test_unsupported_bw_rejected(self):
        with pytest.raises(RadioWriteError, match="not in supported set"):
            validate_lora_params(869.525, 200.0, 11, 5)

    def test_all_supported_bw_accepted(self):
        for bw in LORA_BW_KHZ_VALID:
            validate_lora_params(869.525, bw, 11, 5)

    def test_sf_above_range_rejected(self):
        with pytest.raises(RadioWriteError, match="sf"):
            validate_lora_params(869.525, 250.0, 13, 5)

    def test_sf_below_range_rejected(self):
        with pytest.raises(RadioWriteError, match="sf"):
            validate_lora_params(869.525, 250.0, 4, 5)

    def test_cr_out_of_range_rejected(self):
        with pytest.raises(RadioWriteError, match="cr"):
            validate_lora_params(869.525, 250.0, 11, 9)
        with pytest.raises(RadioWriteError, match="cr"):
            validate_lora_params(869.525, 250.0, 11, 4)

    def test_non_numeric_freq_rejected(self):
        with pytest.raises(RadioWriteError, match="numeric"):
            validate_lora_params("eight-six-nine", 250.0, 11, 5)


class TestTxPowerValidation:
    def test_eu868_caps_at_14(self):
        with pytest.raises(RadioWriteError, match="EU868"):
            validate_tx_power(20, freq_mhz=869.525, radio_max_dbm=22)

    def test_eu868_at_cap_accepted(self):
        assert validate_tx_power(14, freq_mhz=869.525, radio_max_dbm=22) == 14

    def test_us915_allows_30(self):
        assert validate_tx_power(30, freq_mhz=915.0, radio_max_dbm=30) == 30

    def test_us915_rejects_31(self):
        with pytest.raises(RadioWriteError, match="exceeds"):
            validate_tx_power(31, freq_mhz=915.0, radio_max_dbm=30)

    def test_radio_max_overrides_when_lower(self):
        # US allows 30 but radio reports max 22 — 25 should be rejected.
        with pytest.raises(RadioWriteError, match="radio max"):
            validate_tx_power(25, freq_mhz=915.0, radio_max_dbm=22)

    def test_unknown_freq_falls_back_to_30_ceiling(self):
        assert validate_tx_power(30, freq_mhz=2400.0, radio_max_dbm=None) == 30
        with pytest.raises(RadioWriteError):
            validate_tx_power(31, freq_mhz=2400.0, radio_max_dbm=None)

    def test_floor_minus_9(self):
        assert validate_tx_power(-9, freq_mhz=915.0, radio_max_dbm=30) == -9
        with pytest.raises(RadioWriteError, match="floor"):
            validate_tx_power(-10, freq_mhz=915.0, radio_max_dbm=30)


class TestChannelSecret:
    def test_derive_matches_sha256_truncated(self):
        import hashlib
        name = "#mychan"
        expected = hashlib.sha256(name.encode()).digest()[:16]
        assert derive_channel_secret(name) == expected
        assert len(derive_channel_secret(name)) == 16

    def test_parse_explicit_hex(self):
        secret = parse_channel_secret("00112233445566778899aabbccddeeff", "#x")
        assert secret == bytes.fromhex("00112233445566778899aabbccddeeff")

    def test_parse_tolerates_whitespace_in_hex(self):
        secret = parse_channel_secret(
            "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff", "#x",
        )
        assert len(secret) == 16

    def test_parse_invalid_hex_raises(self):
        with pytest.raises(RadioWriteError, match="not valid hex"):
            parse_channel_secret("zzz", "#x")

    def test_parse_wrong_length_raises(self):
        with pytest.raises(RadioWriteError, match="32 hex"):
            parse_channel_secret("aabbcc", "#x")

    def test_parse_no_secret_no_hash_prefix_rejected(self):
        with pytest.raises(RadioWriteError, match="no '#' prefix"):
            parse_channel_secret(None, "plain-name")

    def test_parse_no_secret_hash_name_auto_derives(self):
        secret = parse_channel_secret(None, "#mychan")
        assert secret == derive_channel_secret("#mychan")


class TestChannelNameValidation:
    def test_strips_whitespace(self):
        assert validate_channel_name("  hello  ") == "hello"

    def test_empty_rejected(self):
        with pytest.raises(RadioWriteError, match="empty"):
            validate_channel_name("   ")

    def test_too_long_rejected(self):
        with pytest.raises(RadioWriteError, match="exceeds"):
            validate_channel_name("a" * (CHANNEL_NAME_MAX_LEN + 1))

    def test_null_byte_rejected(self):
        with pytest.raises(RadioWriteError, match="null byte"):
            validate_channel_name("hello\x00bad")


# ─────────────────────────────────────────────────────────────────────
# 2. Setter wrappers — dispatch + cache invalidation
# ─────────────────────────────────────────────────────────────────────


def _setup_mock_meshcore(daemon_handler):
    """Replace handler._meshcore with a MagicMock whose commands return OK."""
    mock_mc = MagicMock()
    mock_mc.commands.set_radio = AsyncMock(return_value=_ok_event())
    mock_mc.commands.set_tx_power = AsyncMock(return_value=_ok_event())
    mock_mc.commands.set_channel = AsyncMock(return_value=_ok_event())
    mock_mc.commands.send_appstart = AsyncMock(return_value=SimpleNamespace(
        type=_FakeEventType.SELF_INFO,
        payload={
            "radio_freq": 915.0, "radio_bw": 250.0,
            "radio_sf": 11, "radio_cr": 5,
            "tx_power": 22, "max_tx_power": 30, "name": "Mock",
        },
    ))
    mock_mc.commands.send_device_query = AsyncMock(return_value=SimpleNamespace(
        type=_FakeEventType.DEVICE_INFO,
        payload={"max_channels": 4, "model": "Mock", "fw_build": "test", "fw ver": 1},
    ))
    mock_mc.commands.get_channel = AsyncMock(return_value=SimpleNamespace(
        type=_FakeEventType.CHANNEL_INFO,
        payload={"channel_idx": 0, "channel_name": "public", "channel_hash": "ab"},
    ))
    daemon_handler._meshcore = mock_mc
    return mock_mc


class TestSetterWrappers:
    def test_set_lora_dispatches_and_refreshes(self, daemon_handler):
        mock_mc = _setup_mock_meshcore(daemon_handler)
        with patch("gateway.meshcore_handler._HAS_MESHCORE", True), \
             patch("gateway.meshcore_handler._meshcore_mod",
                   SimpleNamespace(EventType=_FakeEventType)):
            state = daemon_handler.set_radio_lora(915.0, 250.0, 11, 5)
        mock_mc.commands.set_radio.assert_awaited_once_with(915.0, 250.0, 11, 5)
        # Cache reflects the post-write refresh
        assert state["radio_freq_mhz"] == 915.0
        assert state["radio_sf"] == 11
        assert state["source"] == "radio"

    def test_set_lora_validation_runs_before_dispatch(self, daemon_handler):
        mock_mc = _setup_mock_meshcore(daemon_handler)
        with patch("gateway.meshcore_handler._HAS_MESHCORE", True), \
             patch("gateway.meshcore_handler._meshcore_mod",
                   SimpleNamespace(EventType=_FakeEventType)):
            with pytest.raises(RadioWriteError, match="sf"):
                daemon_handler.set_radio_lora(915.0, 250.0, 13, 5)
        mock_mc.commands.set_radio.assert_not_awaited()

    def test_set_tx_power_uses_cache_for_region_check(self, daemon_handler):
        # Pre-populate cache via the simulator (gives EU 869 MHz, max 22).
        daemon_handler._meshcore = MeshCoreSimulator()
        asyncio.run(daemon_handler._refresh_radio_state())
        snap = daemon_handler.get_radio_state(refresh=False)
        assert snap["radio_freq_mhz"] == 869.525  # EU868 → 14 dBm cap

        mock_mc = _setup_mock_meshcore(daemon_handler)
        with patch("gateway.meshcore_handler._HAS_MESHCORE", True), \
             patch("gateway.meshcore_handler._meshcore_mod",
                   SimpleNamespace(EventType=_FakeEventType)):
            # Cache says 869 MHz → EU868 → 14 dBm cap. 20 dBm rejected.
            with pytest.raises(RadioWriteError, match="EU868|14"):
                daemon_handler.set_radio_tx_power(20)
        mock_mc.commands.set_tx_power.assert_not_awaited()

    def test_set_tx_power_dispatches_and_refreshes(self, daemon_handler):
        mock_mc = _setup_mock_meshcore(daemon_handler)
        with patch("gateway.meshcore_handler._HAS_MESHCORE", True), \
             patch("gateway.meshcore_handler._meshcore_mod",
                   SimpleNamespace(EventType=_FakeEventType)):
            # Refresh once so cache has freq=915 (US — 30 dBm cap).
            asyncio.run(daemon_handler._refresh_radio_state())
            state = daemon_handler.set_radio_tx_power(22)
        mock_mc.commands.set_tx_power.assert_awaited_once_with(22)
        assert state["tx_power_dbm"] == 22

    def test_set_channel_dispatches_with_derived_secret(self, daemon_handler):
        mock_mc = _setup_mock_meshcore(daemon_handler)
        with patch("gateway.meshcore_handler._HAS_MESHCORE", True), \
             patch("gateway.meshcore_handler._meshcore_mod",
                   SimpleNamespace(EventType=_FakeEventType)):
            asyncio.run(daemon_handler._refresh_radio_state())  # populate max_channels
            daemon_handler.set_radio_channel(1, "#testchan")
        # set_channel called with sha256(name)[:16] for the #-prefixed name
        expected_secret = derive_channel_secret("#testchan")
        mock_mc.commands.set_channel.assert_awaited_once_with(
            1, "#testchan", expected_secret,
        )

    def test_set_channel_passes_explicit_hex_secret(self, daemon_handler):
        mock_mc = _setup_mock_meshcore(daemon_handler)
        with patch("gateway.meshcore_handler._HAS_MESHCORE", True), \
             patch("gateway.meshcore_handler._meshcore_mod",
                   SimpleNamespace(EventType=_FakeEventType)):
            asyncio.run(daemon_handler._refresh_radio_state())
            secret_hex = "00" * 16
            daemon_handler.set_radio_channel(2, "plain-name", secret_hex)
        mock_mc.commands.set_channel.assert_awaited_once_with(
            2, "plain-name", bytes.fromhex(secret_hex),
        )

    def test_set_channel_rejects_idx_above_max(self, daemon_handler):
        mock_mc = _setup_mock_meshcore(daemon_handler)
        with patch("gateway.meshcore_handler._HAS_MESHCORE", True), \
             patch("gateway.meshcore_handler._meshcore_mod",
                   SimpleNamespace(EventType=_FakeEventType)):
            asyncio.run(daemon_handler._refresh_radio_state())  # max_channels=4
            with pytest.raises(RadioWriteError, match="max_channels"):
                daemon_handler.set_radio_channel(99, "#x")
        mock_mc.commands.set_channel.assert_not_awaited()

    def test_set_lora_disconnected_raises(self, daemon_handler):
        daemon_handler._meshcore = None
        with pytest.raises(RadioWriteError, match="not connected"):
            daemon_handler.set_radio_lora(915.0, 250.0, 11, 5)

    def test_set_channel_plain_name_no_secret_raises(self, daemon_handler):
        mock_mc = _setup_mock_meshcore(daemon_handler)
        with patch("gateway.meshcore_handler._HAS_MESHCORE", True), \
             patch("gateway.meshcore_handler._meshcore_mod",
                   SimpleNamespace(EventType=_FakeEventType)):
            asyncio.run(daemon_handler._refresh_radio_state())
            with pytest.raises(RadioWriteError, match="no '#' prefix"):
                daemon_handler.set_radio_channel(1, "plain-name", None)
        mock_mc.commands.set_channel.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────
# 3. HTTP PUT /radio/* dispatch
# ─────────────────────────────────────────────────────────────────────


class TestRadioPutEndpoint:
    def test_put_returns_503_when_no_active_handler(self):
        active = get_active_handler()
        if active is not None:
            _clear_active_handler(active)
        body = json.dumps({"freq": 915, "bw": 250, "sf": 11, "cr": 5}).encode()
        h = _make_api_stub("/radio/lora", body=body)
        h._handle_radio_put()
        rsp = _read_response(h)
        assert rsp == {"error": "MeshCore handler not active"}
        h.send_response.assert_called_with(503)

    def test_put_lora_dispatches_to_handler(self, daemon_handler):
        with patch.object(daemon_handler, "set_radio_lora") as mock_set:
            mock_set.return_value = {"radio_freq_mhz": 915.0}
            body = json.dumps({"freq": 915, "bw": 250, "sf": 11, "cr": 5}).encode()
            h = _make_api_stub("/radio/lora", body=body)
            h._handle_radio_put()
        mock_set.assert_called_once_with(freq_mhz=915, bw_khz=250, sf=11, cr=5)
        rsp = _read_response(h)
        assert rsp["radio"]["radio_freq_mhz"] == 915.0

    def test_put_tx_power_dispatches(self, daemon_handler):
        with patch.object(daemon_handler, "set_radio_tx_power") as mock_set:
            mock_set.return_value = {"tx_power_dbm": 17}
            body = json.dumps({"value": 17}).encode()
            h = _make_api_stub("/radio/tx_power", body=body)
            h._handle_radio_put()
        mock_set.assert_called_once_with(dbm=17)

    def test_put_channel_parses_idx_from_path(self, daemon_handler):
        with patch.object(daemon_handler, "set_radio_channel") as mock_set:
            mock_set.return_value = {"channels": []}
            body = json.dumps({"name": "#chan1"}).encode()
            h = _make_api_stub("/radio/channel/2", body=body)
            h._handle_radio_put()
        mock_set.assert_called_once_with(idx=2, name="#chan1", secret_hex=None)

    def test_put_channel_threads_secret_when_given(self, daemon_handler):
        with patch.object(daemon_handler, "set_radio_channel") as mock_set:
            mock_set.return_value = {"channels": []}
            body = json.dumps({"name": "plain", "secret": "00" * 16}).encode()
            h = _make_api_stub("/radio/channel/3", body=body)
            h._handle_radio_put()
        mock_set.assert_called_once_with(idx=3, name="plain", secret_hex="00" * 16)

    def test_put_channel_invalid_idx_returns_400(self, daemon_handler):
        body = json.dumps({"name": "#x"}).encode()
        h = _make_api_stub("/radio/channel/notanint", body=body)
        h._handle_radio_put()
        rsp = _read_response(h)
        assert "Invalid channel idx" in rsp["error"]
        h.send_response.assert_called_with(400)

    def test_put_unknown_radio_path_returns_404(self, daemon_handler):
        h = _make_api_stub("/radio/wat", body=b"{}")
        h._handle_radio_put()
        rsp = _read_response(h)
        assert "Unknown radio path" in rsp["error"]
        h.send_response.assert_called_with(404)

    def test_put_radio_write_error_returns_400(self, daemon_handler):
        with patch.object(
            daemon_handler, "set_radio_lora",
            side_effect=RadioWriteError("freq out of range"),
        ):
            body = json.dumps({"freq": 9999, "bw": 250, "sf": 11, "cr": 5}).encode()
            h = _make_api_stub("/radio/lora", body=body)
            h._handle_radio_put()
        rsp = _read_response(h)
        assert "freq out of range" in rsp["error"]
        h.send_response.assert_called_with(400)

    def test_put_other_exception_returns_500(self, daemon_handler):
        with patch.object(
            daemon_handler, "set_radio_lora",
            side_effect=RuntimeError("kaboom"),
        ):
            body = json.dumps({"freq": 915, "bw": 250, "sf": 11, "cr": 5}).encode()
            h = _make_api_stub("/radio/lora", body=body)
            h._handle_radio_put()
        rsp = _read_response(h)
        assert "Radio write failed" in rsp["error"]
        h.send_response.assert_called_with(500)

    def test_put_non_object_body_returns_400(self, daemon_handler):
        h = _make_api_stub("/radio/lora", body=b"[1,2,3]")
        h._handle_radio_put()
        rsp = _read_response(h)
        assert "JSON object" in rsp["error"]
        h.send_response.assert_called_with(400)

    def test_do_PUT_routes_radio_paths(self, daemon_handler):
        body = json.dumps({"value": 17}).encode()
        h = _make_api_stub("/radio/tx_power", body=body)
        with patch.object(h, "_handle_radio_put") as routed:
            h.do_PUT()
        routed.assert_called_once()

    def test_do_PUT_radio_path_still_localhost_gated(self, daemon_handler):
        h = _make_api_stub("/radio/lora", body=b"{}")
        with patch.object(h, "_check_localhost", return_value=False):
            h.do_PUT()
        h.send_response.assert_called_with(403)


# ─────────────────────────────────────────────────────────────────────
# 4. TUI — double-confirm + region warning
# ─────────────────────────────────────────────────────────────────────


def _make_tui_with_dialog(yesno_returns):
    tui = TUIMeshCoreHandler.__new__(TUIMeshCoreHandler)
    ctx = MagicMock()
    dialog = MagicMock()
    dialog.yesno.side_effect = list(yesno_returns)
    ctx.dialog = dialog
    tui.ctx = ctx
    return tui, dialog


class TestTUIDoubleConfirm:
    _LORA_STATE = {
        "ok": True,
        "radio": {
            "radio_freq_mhz": 869.525, "radio_bw_khz": 250.0,
            "radio_sf": 11, "radio_cr": 5,
        },
    }

    def test_set_lora_aborts_on_first_no(self):
        tui, dialog = _make_tui_with_dialog([False])
        dialog.inputbox.side_effect = ["915.0", "250.0", "11", "5"]
        with patch.object(tui, "_radio_fetch_state", return_value=self._LORA_STATE), \
             patch.object(tui, "_radio_put") as put_mock:
            tui._meshcore_set_lora()
        put_mock.assert_not_called()
        assert dialog.yesno.call_count == 1

    def test_set_lora_aborts_on_second_no(self):
        tui, dialog = _make_tui_with_dialog([True, False])
        dialog.inputbox.side_effect = ["915.0", "250.0", "11", "5"]
        with patch.object(tui, "_radio_fetch_state", return_value=self._LORA_STATE), \
             patch.object(tui, "_radio_put") as put_mock:
            tui._meshcore_set_lora()
        put_mock.assert_not_called()
        assert dialog.yesno.call_count == 2

    def test_set_lora_writes_only_after_both_yes(self):
        tui, dialog = _make_tui_with_dialog([True, True])
        dialog.inputbox.side_effect = ["915.0", "250.0", "11", "5"]
        with patch.object(tui, "_radio_fetch_state", return_value=self._LORA_STATE), \
             patch.object(tui, "_radio_put", return_value={
                 "ok": True, "radio": {"radio_freq_mhz": 915.0},
             }) as put_mock:
            tui._meshcore_set_lora()
        put_mock.assert_called_once_with(
            "lora", {"freq": 915.0, "bw": 250.0, "sf": 11, "cr": 5},
        )

    def test_set_tx_power_double_confirm(self):
        tui, dialog = _make_tui_with_dialog([True, True])
        dialog.inputbox.side_effect = ["20"]
        state = {
            "ok": True,
            "radio": {
                "radio_freq_mhz": 915.0, "tx_power_dbm": 17,
                "max_tx_power_dbm": 30,
            },
        }
        with patch.object(tui, "_radio_fetch_state", return_value=state), \
             patch.object(tui, "_radio_put", return_value={
                 "ok": True, "radio": {"tx_power_dbm": 20},
             }) as put_mock:
            tui._meshcore_set_tx_power()
        put_mock.assert_called_once_with("tx_power", {"value": 20})
        assert dialog.yesno.call_count == 2

    def test_set_channel_double_confirm(self):
        tui, dialog = _make_tui_with_dialog([True, True])
        dialog.menu.return_value = "1"
        dialog.inputbox.side_effect = ["#mychan", ""]
        state = {
            "ok": True,
            "radio": {
                "max_channels": 4,
                "channels": [{"idx": 0, "name": "public", "hash": "ab"}],
            },
        }
        with patch.object(tui, "_radio_fetch_state", return_value=state), \
             patch.object(tui, "_radio_put", return_value={
                 "ok": True, "radio": {"channels": []},
             }) as put_mock:
            tui._meshcore_set_channel()
        put_mock.assert_called_once_with("channel/1", {"name": "#mychan"})
        assert dialog.yesno.call_count == 2


class TestTUIRegionWarning:
    def test_eu_freq_yields_eu868_warning(self):
        msg = TUIMeshCoreHandler._region_warning_for_freq(869.525)
        assert "EU868" in msg
        assert "14 dBm" in msg

    def test_us_freq_yields_us915_warning(self):
        msg = TUIMeshCoreHandler._region_warning_for_freq(915.0)
        assert "US915" in msg
        assert "30 dBm" in msg

    def test_unknown_freq_warns_unknown(self):
        msg = TUIMeshCoreHandler._region_warning_for_freq(2400.0)
        assert "isn't in any known regional band" in msg

    def test_tx_warning_for_eu(self):
        msg = TUIMeshCoreHandler._region_tx_warning(869.525)
        assert "EU868" in msg and "14 dBm" in msg
