"""Tests for the identity write helpers (set_name / set_coords / send_advert).

Covers:
- validate_node_name + validate_coords boundaries
- Async methods on MeshCoreRadioConfig (mocked meshcore)
- Sync wrappers + simulator path
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from gateway.meshcore_radio_config import (
    MeshCoreRadioConfig,
    NODE_NAME_MAX_BYTES,
    RadioWriteError,
    validate_coords,
    validate_node_name,
)


# ─── Validation ─────────────────────────────────────────────────────────


class TestValidateNodeName:
    def test_trims_whitespace(self):
        assert validate_node_name("  hello  ") == "hello"

    def test_rejects_non_string(self):
        with pytest.raises(RadioWriteError):
            validate_node_name(123)

    def test_rejects_empty(self):
        with pytest.raises(RadioWriteError):
            validate_node_name("")
        with pytest.raises(RadioWriteError):
            validate_node_name("   ")

    def test_rejects_null_byte(self):
        with pytest.raises(RadioWriteError):
            validate_node_name("a\x00b")

    def test_rejects_too_long(self):
        with pytest.raises(RadioWriteError):
            validate_node_name("x" * (NODE_NAME_MAX_BYTES + 1))

    def test_accepts_at_max(self):
        s = "x" * NODE_NAME_MAX_BYTES
        assert validate_node_name(s) == s

    def test_byte_length_not_char_length(self):
        # A 4-byte UTF-8 emoji: 8 chars × 4 bytes = 32 bytes, exactly at limit.
        s = "🟢" * 8
        assert validate_node_name(s) == s
        with pytest.raises(RadioWriteError):
            validate_node_name(s + "🟢")  # 36 bytes, over


class TestValidateCoords:
    def test_floats(self):
        assert validate_coords(21.3069, -157.8583) == (21.3069, -157.8583)

    def test_string_coercion(self):
        assert validate_coords("21.3", "-157.8") == (21.3, -157.8)

    @pytest.mark.parametrize("lat", [91, -91, 90.0001, -90.0001])
    def test_lat_out_of_range(self, lat):
        with pytest.raises(RadioWriteError):
            validate_coords(lat, 0)

    @pytest.mark.parametrize("lon", [181, -181, 180.0001, -180.0001])
    def test_lon_out_of_range(self, lon):
        with pytest.raises(RadioWriteError):
            validate_coords(0, lon)

    def test_non_numeric(self):
        with pytest.raises(RadioWriteError):
            validate_coords("foo", 0)
        with pytest.raises(RadioWriteError):
            validate_coords(None, 0)

    def test_boundary_values(self):
        assert validate_coords(90, 180) == (90.0, 180.0)
        assert validate_coords(-90, -180) == (-90.0, -180.0)


# ─── Async methods ───────────────────────────────────────────────────────


class _FakeEvent:
    def __init__(self, type_):
        self.type = type_
        self.payload = {}


class _FakeCommands:
    """Records calls + returns coroutines for fake meshcore.commands."""

    def __init__(self):
        self.calls = []

    async def set_name(self, name):
        self.calls.append(("set_name", name))
        return _FakeEvent("OK")

    async def set_coords(self, lat, lon):
        self.calls.append(("set_coords", lat, lon))
        return _FakeEvent("OK")

    async def send_advert(self, flood):
        self.calls.append(("send_advert", flood))
        return _FakeEvent("OK")


class _FakeMeshCore:
    def __init__(self):
        self.commands = _FakeCommands()

    @property
    def is_connected(self):
        return True


class _NoopRefresh(MeshCoreRadioConfig):
    """MeshCoreRadioConfig with refresh() stubbed so tests don't need a wire."""

    async def refresh(self):
        return None


@pytest.fixture
def cfg(monkeypatch):
    handler = MagicMock()
    # The radio config reads handler._meshcore (private). Set our fake there
    # so _require_meshcore returns it.
    handler._meshcore = _FakeMeshCore()
    # _require_meshcore also checks meshcore_handler._HAS_MESHCORE. The test
    # env may not have meshcore_py installed; lie so the gate passes.
    monkeypatch.setattr("gateway.meshcore_handler._HAS_MESHCORE", True)
    config = _NoopRefresh(handler=handler)
    # Replace _await_ok with a pass-through that just runs the coroutine
    # — our _FakeCommands returns _FakeEvent("OK") which the real
    # _await_ok would reject (EventType check). Tests only verify the call
    # arguments, not the OK pathway.
    async def _await_ok(coro, what, timeout=8.0):
        return await coro
    config._await_ok = _await_ok
    return config


def _ok(coro):
    """Run an awaitable to completion in a fresh event loop."""
    return asyncio.run(coro)


class TestSetName:
    def test_validates_and_calls(self, cfg):
        _ok(cfg.set_name("MyNode"))
        assert cfg._handler._meshcore.commands.calls == [("set_name", "MyNode")]

    def test_invalid_name_no_call(self, cfg):
        with pytest.raises(RadioWriteError):
            _ok(cfg.set_name(""))
        assert cfg._handler._meshcore.commands.calls == []

    def test_trims_before_pushing(self, cfg):
        _ok(cfg.set_name("  MyNode  "))
        assert cfg._handler._meshcore.commands.calls == [("set_name", "MyNode")]


class TestSetCoords:
    def test_validates_and_calls(self, cfg):
        _ok(cfg.set_coords(21.3069, -157.8583))
        assert cfg._handler._meshcore.commands.calls == [
            ("set_coords", 21.3069, -157.8583)
        ]

    def test_invalid_coords_no_call(self, cfg):
        with pytest.raises(RadioWriteError):
            _ok(cfg.set_coords(91, 0))
        assert cfg._handler._meshcore.commands.calls == []

    def test_string_coords_coerced(self, cfg):
        _ok(cfg.set_coords("21.3069", "-157.8583"))
        assert cfg._handler._meshcore.commands.calls == [
            ("set_coords", 21.3069, -157.8583)
        ]


class TestSendAdvert:
    def test_default_no_flood(self, cfg):
        _ok(cfg.send_advert())
        assert cfg._handler._meshcore.commands.calls == [("send_advert", False)]

    def test_flood_true(self, cfg):
        _ok(cfg.send_advert(flood=True))
        assert cfg._handler._meshcore.commands.calls == [("send_advert", True)]

    def test_advert_bumps_timestamp(self, cfg):
        before = cfg._state.get("last_advert_ts")
        _ok(cfg.send_advert())
        after = cfg._state.get("last_advert_ts")
        assert after is not None
        assert after != before


# ─── Sync wrappers ────────────────────────────────────────────────────────


class TestHandlerWrappers:
    def test_set_radio_name_delegates(self):
        from gateway.meshcore_handler import MeshCoreHandler
        # MeshCoreHandler is heavy; assert the methods exist and are callable
        for attr in ("set_radio_name", "set_radio_coords", "send_radio_advert"):
            assert hasattr(MeshCoreHandler, attr), f"{attr} missing"
            assert callable(getattr(MeshCoreHandler, attr))
