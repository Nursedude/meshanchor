"""Companion-radio clock: measure always, write only on opt-in.

Origin (2026-10-31, measured on meshanchor-server's RAK1): the companion's
RTC ran 8.29 days slow (median of last_advert - lastmod over 98 contacts
with sane sender clocks; fresh adverts read sender-age 0.00 d while their
lastmod read 8.3 d). Nothing in the app measured it. The contacts pane had
recorded the symptom on 09-21 and attributed it to firmware semantics.
The radio stamps its own adverts/messages and every contact receipt with
this clock, so the offset is a fact the app must SAY.
"""
import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from gateway import meshcore_clock as mc_clock  # noqa: E402

HOST = 1_793_480_000.0
EIGHT_DAYS = 8.29 * 86400


class _ET:
    CURRENT_TIME = "current_time"
    OK = "ok"
    ERROR = "error"


class _Evt(SimpleNamespace):
    pass


class FakeCommands:
    """A companion whose clock reads ``host + offset`` until set_time."""

    def __init__(self, offset_s, *, get_fails=False, set_fails=False,
                 set_ignored=False):
        self.offset_s = offset_s
        self.get_fails = get_fails
        self.set_fails = set_fails
        self.set_ignored = set_ignored
        self.set_calls = []

    async def get_time(self):
        if self.get_fails:
            return _Evt(type=_ET.ERROR, payload={"reason": "no answer"})
        return _Evt(type=_ET.CURRENT_TIME,
                    payload={"time": int(HOST + self.offset_s)})

    async def set_time(self, val):
        self.set_calls.append(val)
        if self.set_fails:
            return _Evt(type=_ET.ERROR, payload={})
        if not self.set_ignored:
            self.offset_s = val - HOST
        return _Evt(type=_ET.OK, payload={})


def _run(coro):
    return asyncio.run(coro)


# ── judge: the pure verdict ──────────────────────────────────────────────

class TestJudge:
    def test_eight_days_slow_is_skewed_with_signed_offset(self):
        v = mc_clock.judge(HOST - EIGHT_DAYS, HOST, host_synced=True)
        assert v["state"] == "skewed"
        assert v["offset_s"] == pytest.approx(-EIGHT_DAYS, abs=1)

    def test_within_tolerance_is_ok(self):
        v = mc_clock.judge(HOST + 30, HOST, host_synced=True)
        assert v["state"] == "ok"

    @pytest.mark.parametrize("synced", [False, None])
    def test_unsynced_host_is_never_ok_and_never_skewed(self, synced):
        # A stale Pi clock (fake-hwclock, WAN down) cannot judge the radio.
        for dev in (HOST, HOST - EIGHT_DAYS):
            v = mc_clock.judge(dev, HOST, host_synced=synced)
            assert v["state"] == "host_unsynced"
            assert v["offset_s"] is not None  # still reported, labelled

    def test_absent_device_time_is_unobservable_not_zero(self):
        v = mc_clock.judge(None, HOST, host_synced=True)
        assert v["state"] == "unobservable"
        assert v["offset_s"] is None

    def test_zero_device_time_is_unobservable_not_epoch(self):
        # 0 is the absent sentinel, never "56 years slow" (2026-09-02 class).
        v = mc_clock.judge(0, HOST, host_synced=True)
        assert v["state"] == "unobservable"


# ── host NTP sync: tri-state ─────────────────────────────────────────────

class TestHostSynced:
    def _cp(self, out, rc=0):
        return subprocess.CompletedProcess([], rc, stdout=out, stderr="")

    def test_yes(self):
        with patch.object(mc_clock.subprocess, "run", return_value=self._cp("yes\n")):
            assert mc_clock.host_clock_synced() is True

    def test_no(self):
        with patch.object(mc_clock.subprocess, "run", return_value=self._cp("no\n")):
            assert mc_clock.host_clock_synced() is False

    @pytest.mark.parametrize("exc", [FileNotFoundError(), subprocess.TimeoutExpired("t", 3), OSError()])
    def test_cannot_ask_is_none(self, exc):
        with patch.object(mc_clock.subprocess, "run", side_effect=exc):
            assert mc_clock.host_clock_synced() is None

    def test_garbage_is_none(self):
        with patch.object(mc_clock.subprocess, "run", return_value=self._cp("maybe\n")):
            assert mc_clock.host_clock_synced() is None

    def test_passes_a_timeout(self):
        with patch.object(mc_clock.subprocess, "run", return_value=self._cp("yes\n")) as r:
            mc_clock.host_clock_synced()
        assert r.call_args.kwargs.get("timeout")


# ── measure: read-only ───────────────────────────────────────────────────

class TestMeasure:
    def test_reads_offset_and_never_writes(self):
        cmds = FakeCommands(-EIGHT_DAYS)
        v = _run(mc_clock.measure(cmds, _ET, now=lambda: HOST,
                                  host_synced_fn=lambda: True))
        assert v["state"] == "skewed"
        assert cmds.set_calls == []

    def test_error_event_is_unobservable_with_reason(self):
        cmds = FakeCommands(0, get_fails=True)
        v = _run(mc_clock.measure(cmds, _ET, now=lambda: HOST,
                                  host_synced_fn=lambda: True))
        assert v["state"] == "unobservable"
        assert v["reason"]

    def test_exception_is_unobservable_never_raises(self):
        class Boom:
            async def get_time(self):
                raise RuntimeError("serial gone")
        v = _run(mc_clock.measure(Boom(), _ET, now=lambda: HOST,
                                  host_synced_fn=lambda: True))
        assert v["state"] == "unobservable"
        assert "serial gone" in v["reason"]


# ── sync: the write, opt-in and gated ────────────────────────────────────

class TestSyncIfAllowed:
    def _sync(self, cmds, *, allow, synced=True):
        return _run(mc_clock.sync_if_allowed(
            cmds, _ET, allow=allow, now=lambda: HOST,
            host_synced_fn=lambda: synced))

    def test_not_opted_in_never_writes(self):
        cmds = FakeCommands(-EIGHT_DAYS)
        v = self._sync(cmds, allow=False)
        assert cmds.set_calls == []
        assert v["state"] == "skewed"
        assert v["sync"] == "not_opted_in"

    @pytest.mark.parametrize("synced", [False, None])
    def test_unsynced_host_never_writes(self, synced):
        cmds = FakeCommands(-EIGHT_DAYS)
        v = self._sync(cmds, allow=True, synced=synced)
        assert cmds.set_calls == []
        assert v["sync"] == "refused_host_unsynced"

    def test_ok_clock_is_not_rewritten(self):
        cmds = FakeCommands(5)
        v = self._sync(cmds, allow=True)
        assert cmds.set_calls == []
        assert v["sync"] == "not_needed"

    def test_skewed_opted_in_synced_host_writes_and_rereads(self):
        cmds = FakeCommands(-EIGHT_DAYS)
        v = self._sync(cmds, allow=True)
        assert cmds.set_calls == [int(HOST)]
        assert v["sync"] == "synced"
        assert v["state"] == "ok"  # judged from a RE-READ, not assumed
        assert v["offset_before_s"] == pytest.approx(-EIGHT_DAYS, abs=1)

    def test_write_acknowledged_but_ignored_is_not_synced(self):
        # The radio said OK and the clock did not move: say so.
        cmds = FakeCommands(-EIGHT_DAYS, set_ignored=True)
        v = self._sync(cmds, allow=True)
        assert v["sync"] == "failed"
        assert v["state"] == "skewed"

    def test_write_error_is_failed(self):
        cmds = FakeCommands(-EIGHT_DAYS, set_fails=True)
        v = self._sync(cmds, allow=True)
        assert v["sync"] == "failed"
        assert v["state"] == "skewed"

    def test_unobservable_clock_is_never_written(self):
        cmds = FakeCommands(0, get_fails=True)
        v = self._sync(cmds, allow=True)
        assert cmds.set_calls == []
        assert v["state"] == "unobservable"


# ── config + wiring ──────────────────────────────────────────────────────

def test_config_flag_defaults_off():
    from gateway.config import MeshCoreConfig
    assert MeshCoreConfig().sync_clock_on_connect is False


def test_empty_radio_state_carries_clock_slot():
    from gateway.meshcore_radio_config import _empty_radio_state
    assert "clock" in _empty_radio_state()


def test_contacts_snapshot_carries_radio_clock():
    from gateway.meshcore_radio_ops_mixin import MeshCoreRadioOpsMixin

    class H(MeshCoreRadioOpsMixin):
        _connected = True
        _loop = None

        def __init__(self):
            async def gc():
                return SimpleNamespace(type="contacts", payload={})
            self._meshcore = SimpleNamespace(commands=SimpleNamespace(get_contacts=gc))

        @staticmethod
        def _extract_contacts(evt):
            return []

        def get_radio_state(self, refresh=False):
            return {"clock": {"state": "skewed", "offset_s": -EIGHT_DAYS}}

    out = H().get_contacts_snapshot()
    assert out["radio_clock"]["state"] == "skewed"


# ── the consumer paths: connect-time hook + refresh() ────────────────────

def _handler_with(cmds, *, flag):
    from gateway.meshcore_radio_ops_mixin import MeshCoreRadioOpsMixin
    from gateway.meshcore_radio_config import MeshCoreRadioConfig

    class H(MeshCoreRadioOpsMixin):
        pass

    h = H()
    h._meshcore = SimpleNamespace(commands=cmds)
    h._loop = None
    h.config = SimpleNamespace(meshcore=SimpleNamespace(sync_clock_on_connect=flag))
    h._radio = MeshCoreRadioConfig(h)
    return h


@pytest.mark.parametrize("flag,writes", [(False, False), (True, True)])
def test_connect_hook_honours_the_opt_in(flag, writes):
    from gateway import meshcore_handler as mh
    cmds = FakeCommands(-EIGHT_DAYS)
    h = _handler_with(cmds, flag=flag)
    with patch.object(mh, "_HAS_MESHCORE", True), \
         patch.object(mh, "_meshcore_mod", SimpleNamespace(EventType=_ET), create=True), \
         patch.object(mc_clock, "host_clock_synced", return_value=True), \
         patch.object(mc_clock.time, "time", return_value=HOST):
        _run(h._sync_clock_on_connect())
    assert bool(cmds.set_calls) is writes
    clock = h._radio.get_state()["clock"]
    assert clock["state"] == ("ok" if writes else "skewed")
    assert clock["sync"] == ("synced" if writes else "not_opted_in")


def test_refresh_records_the_clock_read_only():
    from gateway import meshcore_handler as mh

    class ET(_ET):
        SELF_INFO = "self_info"
        DEVICE_INFO = "device_info"
        CHANNEL_INFO = "channel_info"

    class Cmds(FakeCommands):
        async def send_appstart(self):
            return _Evt(type=ET.SELF_INFO, payload={"name": "RAK1"})

        async def send_device_query(self):
            return _Evt(type=ET.DEVICE_INFO, payload={"max_channels": 1})

        async def get_channel(self, idx):
            return _Evt(type=ET.ERROR, payload={})

    cmds = Cmds(-EIGHT_DAYS)
    h = _handler_with(cmds, flag=True)  # even opted in, refresh never writes
    with patch.object(mh, "_HAS_MESHCORE", True), \
         patch.object(mh, "_meshcore_mod", SimpleNamespace(EventType=ET), create=True), \
         patch.object(mc_clock, "host_clock_synced", return_value=True), \
         patch.object(mc_clock.time, "time", return_value=HOST):
        _run(h._radio.refresh())
    st = h._radio.get_state()
    assert st["error"] is None and st["node_name"] == "RAK1"
    assert st["clock"]["state"] == "skewed"
    assert cmds.set_calls == []
