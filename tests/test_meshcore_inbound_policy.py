"""Inbound MeshCore source-slot policy (2026-09-18) — built on the wire's
channel_idx, never on a name parsed out of the text.

Every fixture feeds the payload meshcore_py reader.py REALLY emits
({type:'CHAN', channel_idx:int, text:...}); none fabricates ``channel`` or
``is_channel``. The device channel table is stubbed the way the daemon
caches it after connect (0=Public, 1=meshanchor on the live twin).
"""

import asyncio
import logging
import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.meshcore_handler import MeshCoreHandler
from gateway.meshcore_ingress import (
    BRIDGE_CHANNELS_ENV, InboundChannelPolicy, resolve_bridge_channels,
)


def _config(bridge_source_channels=None):
    meshcore = SimpleNamespace(
        enabled=True, device_path='/dev/ttyUSB1', baud_rate=115200,
        connection_type='serial', tcp_host='localhost', tcp_port=4000,
        auto_fetch_messages=True, bridge_channels=True, bridge_dms=True,
        simulation_mode=True, channel_poll_interval_sec=5,
        bridge_source_channels=bridge_source_channels,
    )
    return SimpleNamespace(meshcore=meshcore,
                           meshtastic=SimpleNamespace(host='localhost', port=4403))


def _handler(config=None):
    health = MagicMock(); health.record_error.return_value = 'transient'
    q = Queue(maxsize=100)
    h = MeshCoreHandler(config=config or _config(), node_tracker=MagicMock(),
                        health=health, stop_event=threading.Event(),
                        stats={'errors': 0}, stats_lock=threading.Lock(),
                        message_queue=q)
    # the device channel table as the daemon caches it after connect
    h.get_radio_state = lambda refresh=False: {
        'channels': [{'idx': 0, 'name': 'Public', 'hash': '11'},
                     {'idx': 1, 'name': 'meshanchor', 'hash': '9f'}]}
    h._oracle = None
    return h, q


def _wire_event(idx, text="meshanchor p4: wx"):
    """CHANNEL_MSG_RECV as meshcore_py 2.3.7 reader.py dispatches it."""
    payload = {'type': 'CHAN', 'path_len': 0, 'txt_type': 0,
               'sender_timestamp': 1789767168, 'text': text}
    if idx is not None:
        payload['channel_idx'] = idx
    return SimpleNamespace(type='CHANNEL_MSG_RECV', payload=payload)


def _run(h, event):
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(h._on_channel_message(event))
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv(BRIDGE_CHANNELS_ENV, raising=False)


class TestDefaultPosture:
    def test_public_slot_is_refused_and_the_refusal_is_legible(self, caplog):
        h, q = _handler()
        with caplog.at_level(logging.INFO):
            _run(h, _wire_event(0))
        assert q.empty(), "a Public (slot 0) message reached the bridge queue"
        m = h.get_channel_metrics()
        assert m['event_received'] == 1          # received, so a REFUSAL not blindness
        assert m['channel_suppressed'] == 1
        assert h.stats['meshcore_channel_suppressed'] == 1
        # the line a later reader needs: slot, device name, verdict, reason, the knob
        assert "MeshCore inbound REFUSED slot=0 name=Public" in caplog.text, caplog.text
        assert "Public is refused by default" in caplog.text
        assert BRIDGE_CHANNELS_ENV in caplog.text

    def test_every_refusal_is_visible_at_INFO_not_just_the_first_three(self, caplog):
        """13:13 HST 2026-09-18: refusals 4 and 5 went to DEBUG and the journal
        showed ingress lines with no verdict. A reader must never have to
        infer a refusal from silence."""
        h, q = _handler()
        with caplog.at_level(logging.INFO):
            for _ in range(6):
                _run(h, _wire_event(0))
        lines = [r for r in caplog.records
                 if "inbound REFUSED" in r.getMessage() and r.levelno == logging.INFO]
        assert len(lines) == 6, [r.getMessage()[:60] for r in caplog.records]
        assert "suppressed=6" in lines[-1].getMessage()

    def test_private_slot_bridges(self):
        h, q = _handler()
        _run(h, _wire_event(1))
        assert not q.empty(), "slot 1 (meshanchor) message was dropped"
        assert h.get_channel_metrics()['channel_suppressed'] == 0

    def test_the_live_incident_shape_text_says_meshanchor_but_slot_is_public(self, caplog):
        """The 09-18 leak in one test: a node NAMED 'meshanchor p4' sends on
        PUBLIC. The text contains the private channel's name; the slot says 0.
        The slot wins."""
        h, q = _handler()
        with caplog.at_level(logging.INFO):
            _run(h, _wire_event(0, text="meshanchor p4: cmd"))
        assert q.empty()
        assert "REFUSED slot=0 name=Public" in caplog.text

    def test_missing_slot_is_refused_as_unknown_not_treated_as_private(self, caplog):
        h, q = _handler()
        with caplog.at_level(logging.INFO):
            _run(h, _wire_event(None))
        assert q.empty()
        assert "slot unknown" in caplog.text, caplog.text

    def test_missing_slot_is_not_admitted_even_when_Public_is_opted_in(self, monkeypatch):
        monkeypatch.setenv(BRIDGE_CHANNELS_ENV, "0")
        h, q = _handler()
        _run(h, _wire_event(None))
        assert q.empty()


class TestExplicitPosture:
    def test_env_admits_public_when_listed(self, monkeypatch):
        monkeypatch.setenv(BRIDGE_CHANNELS_ENV, "0,1")
        h, q = _handler()
        _run(h, _wire_event(0))
        assert not q.empty()

    def test_env_list_is_exact_not_additive(self, monkeypatch):
        monkeypatch.setenv(BRIDGE_CHANNELS_ENV, "2")
        h, q = _handler()
        _run(h, _wire_event(1))
        assert q.empty(), "slot 1 bridged although only slot 2 is listed"

    def test_empty_env_means_bridge_nothing(self, monkeypatch):
        monkeypatch.setenv(BRIDGE_CHANNELS_ENV, "")
        h, q = _handler()
        _run(h, _wire_event(1))
        assert q.empty()

    def test_declared_config_used_when_env_absent(self):
        h, q = _handler(_config(bridge_source_channels=[0]))
        _run(h, _wire_event(0)); assert not q.empty()
        h2, q2 = _handler(_config(bridge_source_channels=[0]))
        _run(h2, _wire_event(1)); assert q2.empty()

    def test_env_overrides_declared_config(self, monkeypatch):
        monkeypatch.setenv(BRIDGE_CHANNELS_ENV, "1")
        h, q = _handler(_config(bridge_source_channels=[0]))
        _run(h, _wire_event(0)); assert q.empty()

    def test_typo_is_loud_and_does_not_widen_the_gate(self, monkeypatch, caplog):
        monkeypatch.setenv(BRIDGE_CHANNELS_ENV, "1,pubic")
        with caplog.at_level(logging.WARNING):
            allowed, source = resolve_bridge_channels(None)
        assert allowed == frozenset({1}) and source == "env"
        assert "not a slot index; ignored" in caplog.text


class TestOracleGateUsesTheDeviceName:
    def test_oracle_receives_the_slot_name_from_the_device_table_not_the_text(self):
        h, q = _handler()
        h._oracle = MagicMock(); h._oracle.handle.return_value = None
        _run(h, _wire_event(1, text="Somebody Else: status"))
        h._oracle.handle.assert_called_once_with("Else", "status", "meshanchor")

    def test_public_query_from_a_node_named_meshanchor_gates_as_public(self):
        """The leak in oracle form: the text says 'meshanchor', the slot is 0.
        The oracle must be told 'public'."""
        h, q = _handler()
        h._oracle = MagicMock(); h._oracle.handle.return_value = None
        _run(h, _wire_event(0, text="meshanchor p4: status"))
        h._oracle.handle.assert_called_once_with("p4", "status", "public")

    def test_unknown_slot_name_gates_fail_closed_and_is_never_None(self):
        """Fail-closed — but NOT by passing None.

        Until 2026-09-21 an unnamed slot gated as None, and the oracle's reply
        closure uses ``channel is None`` to mean "this is a DM": an unnamed
        slot therefore took the DM branch and answered whichever contact the
        SENDER named in the message text. The token is now a sentinel that no
        configured channel name can equal, so the gate is still closed and the
        two legs stay distinguishable. This asserts the PROPERTY against the
        real gate, not the literal the old test pinned.
        """
        from gateway.meshcore_ingress import UNNAMED_SLOT
        from oracle.responder import MeshOracleResponder

        h, q = _handler()
        h.get_radio_state = lambda refresh=False: {'channels': []}
        h._oracle = MagicMock(); h._oracle.handle.return_value = None
        _run(h, _wire_event(1, text="meshanchor p4: status"))
        h._oracle.handle.assert_called_once_with("p4", "status", UNNAMED_SLOT)
        assert UNNAMED_SLOT is not None

        gate = MeshOracleResponder(
            snapshot_fn=lambda: None, send_fn=lambda *a: True,
            allowed_channels={"meshanchor", "public", ""})
        assert gate._allowed("!p4", UNNAMED_SLOT) is False
        assert gate._allowed("!p4", "meshanchor") is True  # control: can pass


class TestLegibility:
    def test_posture_line_names_allow_source_device_table_and_the_knob(self, caplog):
        h, _ = _handler()
        with caplog.at_level(logging.INFO):
            h._inbound_policy.log_posture(h)
        line = [r.getMessage() for r in caplog.records if "inbound channel policy" in r.getMessage()][0]
        assert "allow=all slots except Public(0)" in line
        assert "source=default" in line
        assert "device-channels=0=Public 1=meshanchor" in line
        assert BRIDGE_CHANNELS_ENV in line

    def test_posture_line_says_when_the_table_is_not_read_yet(self, caplog):
        h, _ = _handler()
        h.get_radio_state = lambda refresh=False: {'channels': []}
        with caplog.at_level(logging.INFO):
            h._inbound_policy.log_posture(h)
        assert "device-channels=(not yet read)" in caplog.text

    def test_ingress_line_carries_the_device_name(self, caplog):
        h, _ = _handler()
        with caplog.at_level(logging.INFO):
            _run(h, _wire_event(1))
        assert "MeshCore channel rx idx=1 name=meshanchor" in caplog.text

    def test_ingress_line_carries_reach_hops_and_snr_from_the_wire(self, caplog):
        """path_len + SNR ride on every CHANNEL_MSG_RECV and were read by
        nothing. 3 repeater hops at -7.25 dB must render as such; a direct
        packet (path_len 0 or the 255 marker) as 'direct'; absent as '?'."""
        from gateway.meshcore_ingress import reach_of
        assert reach_of({'path_len': 3, 'SNR': -7.25}) == "hops=3 snr=-7.25"
        assert reach_of({'path_len': 0, 'SNR': 9.5}) == "hops=direct snr=9.5"
        assert reach_of({'path_len': 255}) == "hops=direct snr=?"
        assert reach_of({'text': 'x'}) == "hops=? snr=?"
        assert reach_of(None) == "hops=? snr=?"
        h, _ = _handler()
        ev = _wire_event(1); ev.payload['SNR'] = -7.25; ev.payload['path_len'] = 3
        with caplog.at_level(logging.INFO):
            _run(h, ev)
        assert "channel rx idx=1 name=meshanchor hops=3 snr=-7.25" in caplog.text, caplog.text

    def test_metrics_line_carries_the_suppression_count_and_posture(self, caplog):
        h, _ = _handler()
        _run(h, _wire_event(0))
        with caplog.at_level(logging.INFO):
            h._log_channel_metrics()
        assert "suppressed=1 (allow=all slots except Public(0) source=default)" in caplog.text

    def test_describe_empty_allowlist_reads_as_dm_only(self, monkeypatch):
        monkeypatch.setenv(BRIDGE_CHANNELS_ENV, "")
        assert "NOTHING" in InboundChannelPolicy(None).describe()
