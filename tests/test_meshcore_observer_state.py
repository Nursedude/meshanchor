"""CS-1 observer witness STATE — one derivation, the whole input space (v2).

Spec: ~/.claude/plans/cs1_r1_review_2026_11_18/STATE_TABLE_v2.md. r1 found
state decided from one flag at a time; r2 found the next level of the same
kind — state read CACHED setup facts (M1 class-default enabled, I1 mid-init,
I2 re-subscribe, I3 radio gone). v2: every input is read LIVE at snapshot
time, or is bound to the radio session it was taken on.

  1 enabled False             -> disabled   (+ config_error if not a bool)
  2 mode supervisor           -> unsupported_supervisor_mode
  3 radio down                -> no_radio
  4 setup not_attempted       -> not_subscribed   (not wired to THIS session)
  5 setup no_client           -> no_mqtt_client
  6 setup client_raised       -> client_error
  7 setup subscribe_raised    -> subscribe_failed
  8 last_outcome none         -> never
  9 last_outcome queued       -> ok        ("queued" = rc 0 at paho, NOT delivered)
 10 last_outcome refused      -> refusing
 11 last_outcome publish_failed / internal_error -> failing
"""

import itertools
import threading
from types import SimpleNamespace

import pytest

import gateway.meshcore_observer as obs
import gateway.meshcore_observer_mixin as mixin
from tests.test_meshcore_observer import (KEY, _bare_handler, _event_payload,
                                          _EventType, _FakeMeshCore,
                                          _FakePublisher, _fire)

ENABLED = (False, True)
MODES = ("handler", "supervisor")
RADIOS = ("up", "down")
SETUPS = ("not_attempted", "no_client", "client_raised", "subscribe_raised", "ok")
OUTCOMES = ("none", "queued", "refused", "publish_failed", "internal_error")

RUNTIME_STATES = {"never", "ok", "refusing", "failing"}

# The table, literally: (predicate over the row, state). First match wins.
TABLE = (
    (lambda r: not r["enabled"], "disabled"),
    (lambda r: r["mode"] == "supervisor", "unsupported_supervisor_mode"),
    (lambda r: r["radio"] == "down", "no_radio"),
    (lambda r: r["setup"] == "not_attempted", "not_subscribed"),
    (lambda r: r["setup"] == "no_client", "no_mqtt_client"),
    (lambda r: r["setup"] == "client_raised", "client_error"),
    (lambda r: r["setup"] == "subscribe_raised", "subscribe_failed"),
    (lambda r: r["last_outcome"] == "none", "never"),
    (lambda r: r["last_outcome"] == "queued", "ok"),
    (lambda r: r["last_outcome"] == "refused", "refusing"),
    (lambda r: r["last_outcome"] == "publish_failed", "failing"),
    (lambda r: r["last_outcome"] == "internal_error", "failing"),
)

ROWS = [dict(zip(("enabled", "mode", "radio", "setup", "last_outcome"), v))
        for v in itertools.product(ENABLED, MODES, RADIOS, SETUPS, OUTCOMES)]


def _expected(row):
    return next(state for pred, state in TABLE if pred(row))


def test_the_table_covers_the_whole_input_space_with_no_fall_through():
    assert len(ROWS) == 200
    for row in ROWS:   # every row matches at least one line (no fall-through)
        assert any(pred(row) for pred, _ in TABLE), row


@pytest.mark.parametrize("row", ROWS, ids=lambda r: "-".join(str(v) for v in r.values()))
def test_derive_state_matches_the_table_on_every_row(row):
    assert obs.derive_state(**row) == _expected(row)


def test_disabled_wins_over_every_stale_fact():
    states = {obs.derive_state(**r) for r in ROWS if not r["enabled"]}
    assert states == {"disabled"}


def test_a_setup_fault_or_a_down_radio_never_reads_as_a_runtime_state():
    faulty = [r for r in ROWS if r["enabled"] and (
        r["mode"] == "supervisor" or r["radio"] == "down" or r["setup"] != "ok")]
    for r in faulty:
        assert obs.derive_state(**r) not in RUNTIME_STATES, r


def test_a_down_radio_never_reads_ok_whatever_the_last_packet_did():
    # I3: radio gone after one good packet stayed "ok".
    for r in ROWS:
        if r["enabled"] and r["mode"] == "handler" and r["radio"] == "down":
            assert obs.derive_state(**r) == "no_radio", r


@pytest.mark.parametrize("enabled", [1, 0, "false", "true", None])
def test_derive_state_refuses_an_enabled_that_is_not_a_bool(enabled):
    # LOW-4: the string "false" is truthy and switched the observer ON.
    with pytest.raises(ValueError):
        obs.derive_state(enabled=enabled, mode="handler", radio="up",
                         setup="ok", last_outcome="none")


def test_derive_state_refuses_a_value_outside_the_vocabulary():
    # hfm #7: a new outcome must fail a test until the table is updated.
    with pytest.raises(ValueError):
        obs.derive_state(enabled=True, mode="handler", radio="up",
                         setup="ok", last_outcome="delivered")


# ---------------------------------------------------------------------------
# The handler feeds the derivation the TRUE inputs (wiring, end to end)
# ---------------------------------------------------------------------------

def _on(pub=None, mc=None, iata="ITO"):
    mc = mc if mc is not None else _FakeMeshCore()
    h = _bare_handler(enabled=True, iata=iata, mc=mc)
    h._init_observer(_EventType, publisher=pub if pub is not None else _FakePublisher())
    return h, mc


def test_every_packet_refused_reads_refusing_not_never():
    # MED-2: enabled with the default empty IATA refused 50, said "never".
    h, mc = _on(iata="")
    for _ in range(3):
        _fire(mc, _event_payload())

    snap = h.observer_snapshot()
    assert snap["state"] == "refusing"
    assert snap["last_refused"] == "bad_iata"
    assert snap["last_outcome_run"] == 3


def test_ok_then_the_key_is_lost_reads_refusing_not_ok():
    # MED-2: one good packet then 100 bad_observer_key still said "ok".
    h, mc = _on()
    _fire(mc, _event_payload())
    mc.self_info = {}
    _fire(mc, _event_payload())

    assert h.observer_snapshot()["state"] == "refusing"


def test_an_internal_bug_reads_failing_never_ok_or_never():
    h, mc = _on()
    _fire(mc, _event_payload())

    import gateway.meshcore_observer_mixin as m
    orig = m.build_packet_message
    try:
        m.build_packet_message = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug"))
        _fire(mc, _event_payload())
    finally:
        m.build_packet_message = orig

    snap = h.observer_snapshot()
    assert snap["state"] == "failing"
    assert "bug" in snap["last_error"]


def test_recovery_reads_ok_and_clears_last_error():
    pub = _FakePublisher(error=ConnectionError("broker down"))
    h, mc = _on(pub=pub)
    _fire(mc, _event_payload())
    assert h.observer_snapshot()["state"] == "failing"

    pub.error = None
    _fire(mc, _event_payload())

    snap = h.observer_snapshot()
    assert snap["state"] == "ok"
    assert snap["last_error"] is None
    assert snap["last_outcome_run"] == 1


def test_the_counter_says_queued_not_published():
    # LOW-8 / D2: paho rc 0 = handed to the client; a stalled broker "published" 30,000.
    h, mc = _on()
    _fire(mc, _event_payload())

    snap = h.observer_snapshot()
    assert snap["queued"] == 1
    assert "published" not in snap


def test_a_factory_that_raises_reads_client_error_never_disabled(monkeypatch):
    # LOW-3: paho 1.x has no CallbackAPIVersion; the raise escaped -> "disabled".
    def _boom():
        raise AttributeError("module 'paho.mqtt.client' has no attribute 'CallbackAPIVersion'")
    monkeypatch.setattr(mixin, "make_local_publisher", _boom)
    mc = _FakeMeshCore()
    h = _bare_handler(enabled=True, mc=mc)

    h._init_observer(_EventType)          # must not raise into connect

    snap = h.observer_snapshot()
    assert snap["state"] == "client_error"
    assert "CallbackAPIVersion" in snap["last_error"]
    assert mc.subscribed == []


def test_no_mqtt_client_is_not_sticky_across_a_reconnect(monkeypatch):
    # LOW-5: the flag stayed True while the observer published.
    monkeypatch.setattr(mixin, "make_local_publisher", lambda: None)
    h = _bare_handler(enabled=True)
    h._init_observer(_EventType)
    assert h.observer_snapshot()["state"] == "no_mqtt_client"

    monkeypatch.setattr(mixin, "make_local_publisher", lambda: _FakePublisher())
    h._meshcore = _FakeMeshCore()
    h._init_observer(_EventType)

    assert h.observer_snapshot()["state"] == "never"


def test_disabling_drops_the_stale_ok_and_its_counters():
    # LOW-5: enabled -> disabled kept reading "ok".
    h, mc = _on()
    _fire(mc, _event_payload())
    assert h.observer_snapshot()["state"] == "ok"

    h.config.meshcore.observer_enabled = False
    h._meshcore = _FakeMeshCore()
    h._init_observer(_EventType)

    assert h.observer_snapshot() == {"state": "disabled"}


def test_a_re_enable_starts_a_fresh_record_not_the_old_ok():
    h, mc = _on()
    _fire(mc, _event_payload())
    h.config.meshcore.observer_enabled = False
    h._init_observer(_EventType)

    h.config.meshcore.observer_enabled = True
    h._meshcore = _FakeMeshCore()
    h._init_observer(_EventType, publisher=_FakePublisher())

    snap = h.observer_snapshot()
    assert snap["state"] == "never"      # no packet since re-enable
    assert snap["queued"] == 0


def test_counters_and_last_outcome_survive_a_reconnect():
    # m25: a witness re-created per reconnect zeroed the record.
    pub = _FakePublisher()
    h, mc = _on(pub=pub)
    _fire(mc, _event_payload())

    mc2 = _FakeMeshCore()
    h._meshcore = mc2
    h._init_observer(_EventType, publisher=pub)

    snap = h.observer_snapshot()
    assert snap["state"] == "ok"
    assert snap["queued"] == 1


def test_the_subscription_is_kept_so_disconnect_can_drop_it():
    # m26: a sub not in _subscriptions is never unsubscribed.
    h, mc = _on()

    assert len(h._subscriptions) == 1


def test_a_snapshot_is_never_torn_by_a_writer_thread():
    # INFO r1: 72,311 torn of 298,836 (queued != sum of buckets) without a lock.
    h, mc = _on()
    msg = obs.build_packet_message(_event_payload(), iata="ITO", observer_key=KEY)
    w = h._observer_witness
    stop = threading.Event()

    def _writer():
        while not stop.is_set():
            w.record_queued(msg)
    t = threading.Thread(target=_writer, daemon=True)
    t.start()
    try:
        torn = 0
        for _ in range(20000):
            s = h.observer_snapshot()
            if s["queued"] != sum(s["length_buckets"].values()) + s["ceiling_contradicted"]:
                torn += 1
    finally:
        stop.set()
        t.join(timeout=5)
    assert torn == 0


# ---------------------------------------------------------------------------
# Supervisor mode (MED-1 / D3): no observer, but never silently inert
# ---------------------------------------------------------------------------

def _supervisor(enabled):
    from gateway.meshcore_supervisor_handler import MeshCoreSupervisorHandler
    h = MeshCoreSupervisorHandler.__new__(MeshCoreSupervisorHandler)
    h.config = SimpleNamespace(meshcore=SimpleNamespace(
        observer_enabled=enabled, observer_iata="ITO"))
    return h


def test_supervisor_mode_with_the_observer_on_says_unsupported_and_warns(caplog):
    h = _supervisor(enabled=True)

    with caplog.at_level("WARNING"):
        h._init_observer()

    assert h.observer_snapshot() == {"state": "unsupported_supervisor_mode"}
    assert any("observer" in r.message and r.levelname == "WARNING" for r in caplog.records)


def test_supervisor_mode_with_the_observer_off_says_disabled_quietly(caplog):
    h = _supervisor(enabled=False)

    with caplog.at_level("WARNING"):
        h._init_observer()

    assert h.observer_snapshot() == {"state": "disabled"}
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_supervisor_connect_really_runs_the_observer_check(monkeypatch, caplog):
    # r2 s03: removing the _init_observer call from connect() left tests green,
    # so the MED-1 warning could be dead in production.
    import gateway.meshcore_supervisor_handler as sup

    class _FakeSupClient:
        connected = True

        def __init__(self, **kw):
            pass

        def connect(self):
            return {"connected": True}

        def on_event(self, *a):
            pass

        def __getattr__(self, n):
            return lambda *a, **k: None

    monkeypatch.setattr(sup, "MeshCoreSupervisorClient", _FakeSupClient)
    monkeypatch.setattr(sup, "_set_active_handler", lambda h: None)
    cfg = SimpleNamespace(meshcore=SimpleNamespace(observer_enabled=True, observer_iata="ITO"))
    h = sup.MeshCoreSupervisorHandler(cfg, None, SimpleNamespace(), threading.Event(),
                                      {}, threading.Lock(), None)
    with caplog.at_level("WARNING"):
        h.connect()

    assert [r for r in caplog.records
            if r.levelname == "WARNING" and "observer" in r.getMessage()]


# ===========================================================================
# v2 (Fable r2): every input LIVE, or bound to the radio session it came from
# ===========================================================================

def test_enabled_before_any_connect_reads_no_radio_not_disabled():
    # M1: the class default made an ENABLED observer read "disabled".
    h = _bare_handler(enabled=True)
    h._connected, h._meshcore = False, None

    assert h.observer_snapshot()["state"] == "no_radio"


def test_radio_up_but_observer_never_wired_reads_not_subscribed():
    # M1 N1b/N1c: an earlier subscribe raised, or simulation mode, so
    # _init_observer never ran on this session.
    h = _bare_handler(enabled=True)

    assert h.observer_snapshot()["state"] == "not_subscribed"


def test_a_disconnect_after_ok_reads_no_radio_not_ok():
    # I3: the radio gone after one good packet stayed "ok".
    h, mc = _on()
    _fire(mc, _event_payload())
    h._connected, h._meshcore = False, None      # what disconnect() leaves

    assert h.observer_snapshot()["state"] == "no_radio"


def test_a_lost_connection_reads_no_radio_while_the_session_object_lingers():
    # _handle_disconnection sets _connected False BEFORE the cleanup drops
    # _meshcore (meshcore_handler.py) — r2-fix mutant v14.
    h, mc = _on()
    _fire(mc, _event_payload())
    h._connected = False

    assert h.observer_snapshot()["state"] == "no_radio"


def test_no_session_object_reads_no_radio_even_if_flagged_connected():
    # mutant v15.
    h, mc = _on()
    h._meshcore = None

    assert h.observer_snapshot()["state"] == "no_radio"


def test_a_failed_subscribe_is_retried_on_the_same_session():
    # mutant v18: the same-session no-op must hold only after a CLEAN wiring.
    mc = _FakeMeshCore()
    real_subscribe = mc.subscribe
    mc.subscribe = None
    h = _bare_handler(enabled=True, mc=mc)
    pub = _FakePublisher()
    h._init_observer(_EventType, publisher=pub)
    assert h.observer_snapshot()["state"] == "subscribe_failed"

    mc.subscribe = real_subscribe
    h._init_observer(_EventType, publisher=pub)

    assert h.observer_snapshot()["state"] == "never"
    assert len(mc.subscribed) == 1


def test_disable_then_enable_on_the_same_session_stays_wired_once():
    # v4 (Fable r4 L3): the v3 version of this test PINNED a double
    # subscription (every frame published twice). Disable keeps the
    # subscription and resets the witness in place; re-enable is a no-op wiring.
    pub = _FakePublisher()
    h, mc = _on(pub=pub)
    _fire(mc, _event_payload())
    h.config.meshcore.observer_enabled = False
    h._init_observer(_EventType)
    for _ in range(3):
        _fire(mc, _event_payload())                 # heard while disabled
    assert h._observer_witness.dropped() == {"disabled": 3}   # counted, never silent

    h.config.meshcore.observer_enabled = True
    h._init_observer(_EventType, publisher=pub)

    assert len(mc.subscribed) == 1                  # ONE subscription, ever
    snap = h.observer_snapshot()
    assert (snap["state"], snap["queued"]) == ("never", 0)   # a fresh record
    _fire(mc, _event_payload())
    assert len(pub.sent) == 2                       # 1 before + 1 now: never twice


def test_supervisor_mode_with_a_truthy_non_bool_is_disabled_quietly(caplog):
    # mutant v32.
    h = _supervisor(enabled=1)
    with caplog.at_level("WARNING"):
        h._init_observer()

    assert h.observer_snapshot() == {"state": "disabled"}
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_a_new_radio_session_not_yet_wired_reads_not_subscribed():
    h, mc = _on()
    _fire(mc, _event_payload())
    h._meshcore = _FakeMeshCore()                # reconnected, init not run yet

    assert h.observer_snapshot()["state"] == "not_subscribed"


def test_enabled_is_read_live_from_the_config_never_cached():
    h, mc = _on()
    h.config.meshcore.observer_enabled = False
    assert h.observer_snapshot() == {"state": "disabled"}

    h.config.meshcore.observer_enabled = True
    assert h.observer_snapshot()["state"] == "never"


def test_a_truthy_non_bool_enabled_never_wires_the_observer():
    # r2 m01: `is True` -> truthy went unpinned on the handler.
    mc = _FakeMeshCore()
    h = _bare_handler(enabled=1, mc=mc)
    h._init_observer(_EventType, publisher=_FakePublisher())

    assert mc.subscribed == []
    assert h.observer_snapshot() == {"state": "disabled"}


def test_a_second_init_on_the_same_session_is_a_noop():
    # r1 INFO: double _init_observer on one session double-published.
    pub = _FakePublisher()
    h, mc = _on(pub=pub)
    h._init_observer(_EventType, publisher=pub)
    _fire(mc, _event_payload())

    assert len(mc.subscribed) == 1
    assert len(pub.sent) == 1
    assert h.observer_snapshot()["queued"] == 1


def test_a_failing_resubscribe_cannot_mask_a_live_observer():
    # I2: re-subscribe raised on the same session -> "subscribe_failed" while publishing.
    pub = _FakePublisher()
    h, mc = _on(pub=pub)
    mc.subscribe = None
    h._init_observer(_EventType, publisher=pub)
    (_, cb), = [(e, c) for e, c in mc.subscribed]
    import asyncio
    asyncio.run(cb(SimpleNamespace(payload=_event_payload())))

    assert h.observer_snapshot()["state"] == "ok"


def test_a_snapshot_during_a_slow_init_reads_not_subscribed_not_never(monkeypatch):
    # I1: setup facts were reset BEFORE the slow factory; a reader mid-init saw "never".
    monkeypatch.setattr(mixin, "make_local_publisher", lambda: None)
    h = _bare_handler(enabled=True)
    h._init_observer(_EventType)
    assert h.observer_snapshot()["state"] == "no_mqtt_client"

    entered, release = threading.Event(), threading.Event()

    def _slow():
        entered.set()
        release.wait(5)
        return None
    monkeypatch.setattr(mixin, "make_local_publisher", _slow)
    h._meshcore = _FakeMeshCore()
    t = threading.Thread(target=h._init_observer, args=(_EventType,), daemon=True)
    t.start()
    try:
        assert entered.wait(5)
        during = h.observer_snapshot()["state"]
    finally:
        release.set()
        t.join(5)

    assert during == "not_subscribed"
    assert h.observer_snapshot()["state"] == "no_mqtt_client"


def test_a_setup_error_clears_when_the_next_session_wires_cleanly():
    # r2 m04: a sticky setup error survived a clean reconnect.
    mc = _FakeMeshCore()
    mc.subscribe = None
    h = _bare_handler(enabled=True, mc=mc)
    pub = _FakePublisher()
    h._init_observer(_EventType, publisher=pub)
    assert h.observer_snapshot()["state"] == "subscribe_failed"

    h._meshcore = _FakeMeshCore()
    h._init_observer(_EventType, publisher=pub)

    snap = h.observer_snapshot()
    assert snap["state"] == "never"
    assert snap["last_error"] is None


def test_an_old_sessions_callback_after_disable_neither_raises_nor_publishes():
    # r2 m23: the stale-callback guard was unpinned.
    import asyncio
    pub = _FakePublisher()
    h, mc = _on(pub=pub)
    (_, cb), = mc.subscribed
    h.config.meshcore.observer_enabled = False
    h._meshcore = _FakeMeshCore()
    h._init_observer(_EventType)

    asyncio.run(cb(SimpleNamespace(payload=_event_payload())))    # must not raise

    assert pub.sent == []


def test_a_lowercase_iata_is_refused_not_quietly_uppercased():
    # r2 m24.
    h, mc = _on(iata="ito")
    _fire(mc, _event_payload())

    snap = h.observer_snapshot()
    assert (snap["state"], snap["last_refused"]) == ("refusing", "bad_iata")


def test_a_reconnect_uses_the_publisher_it_is_handed():
    # r2 m28.
    pub1, pub2 = _FakePublisher(), _FakePublisher()
    h, mc = _on(pub=pub1)
    mc2 = _FakeMeshCore()
    h._meshcore = mc2
    h._init_observer(_EventType, publisher=pub2)
    _fire(mc2, _event_payload())

    assert (len(pub1.sent), len(pub2.sent)) == (0, 1)


# --- D5: a non-bool config value is OFF and SAYS so ------------------------

def test_a_typed_string_enabled_reads_disabled_with_a_config_error():
    from gateway.config import MeshCoreConfig
    from dataclasses import asdict
    h = _bare_handler(enabled=False)
    h.config = SimpleNamespace(meshcore=MeshCoreConfig(observer_enabled="true",
                                                       observer_iata="ITO"))
    h._init_observer(_EventType, publisher=_FakePublisher())

    snap = h.observer_snapshot()
    assert snap["state"] == "disabled"
    assert "observer_enabled" in snap["config_error"]
    # the witness of the error is never SAVED back into gateway.json
    assert not any("error" in k for k in asdict(h.config.meshcore))


def test_supervisor_mode_carries_the_config_error_too():
    from gateway.config import MeshCoreConfig
    h = _supervisor(enabled=False)
    h.config = SimpleNamespace(meshcore=MeshCoreConfig(observer_enabled="true"))

    snap = h.observer_snapshot()
    assert snap["state"] == "disabled"
    assert "observer_enabled" in snap["config_error"]


# --- L1: loud at the transition, never per packet --------------------------

def _warnings(caplog, word):
    return [r for r in caplog.records
            if r.levelname == "WARNING" and word in r.getMessage().lower()]


def test_an_iata_that_will_refuse_everything_warns_at_init(caplog):
    with caplog.at_level("WARNING"):
        _on(iata="")

    assert len(_warnings(caplog, "iata")) == 1


class _FakeMonotonic:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _on_with_clock(monkeypatch, pub=None, mc=None, iata="ITO"):
    clock = _FakeMonotonic()
    monkeypatch.setattr(mixin.time, "monotonic", clock)
    h, mc = _on(pub=pub, mc=mc, iata=iata)
    return h, mc, clock


def test_refusing_warns_once_per_run_not_per_packet(caplog, monkeypatch):
    h, mc, clock = _on_with_clock(monkeypatch)
    _fire(mc, _event_payload())
    good = mc.self_info
    with caplog.at_level("INFO"):
        mc.self_info = {}
        for _ in range(50):
            _fire(mc, _event_payload())
        assert len(_warnings(caplog, "refus")) == 1
        # one RUN is one transition: nothing was suppressed (r3-on-v3 m13)
        assert h.observer_snapshot()["log_lines_suppressed"] == 0

        mc.self_info = good
        _fire(mc, _event_payload())                    # recovered
        assert [r for r in caplog.records
                if r.levelname == "INFO" and "recover" in r.getMessage().lower()]

        clock.t += 301                                 # past the v3 rate window
        mc.self_info = {}
        _fire(mc, _event_payload())                    # a NEW run
    assert len(_warnings(caplog, "refus")) == 2


def test_failing_warns_once_per_run(caplog):
    pub = _FakePublisher(error=ConnectionError("broker down"))
    h, mc = _on(pub=pub)
    with caplog.at_level("WARNING"):
        for _ in range(10):
            _fire(mc, _event_payload())

    assert len(_warnings(caplog, "fail")) == 1


def test_an_internal_error_warns(caplog, monkeypatch):
    # r2 m27.
    h, mc = _on()
    monkeypatch.setattr(mixin, "build_packet_message",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")))
    with caplog.at_level("WARNING"):
        _fire(mc, _event_payload())

    assert _warnings(caplog, "internal")


@pytest.mark.parametrize("fault", ["no_client", "client_raised", "subscribe_raised"])
def test_every_setup_fault_warns(fault, monkeypatch, caplog):
    # r2 m20 m21 m22: each LOUD warning could be downgraded unseen.
    mc = _FakeMeshCore()
    if fault == "no_client":
        monkeypatch.setattr(mixin, "make_local_publisher", lambda: None)
    elif fault == "client_raised":
        monkeypatch.setattr(mixin, "make_local_publisher",
                            lambda: (_ for _ in ()).throw(AttributeError("CallbackAPIVersion")))
    else:
        mc.subscribe = None
    h = _bare_handler(enabled=True, mc=mc)
    with caplog.at_level("WARNING"):
        h._init_observer(_EventType)

    assert _warnings(caplog, "observer")


# --- the local publisher factory (r2 p03 p04 p05) --------------------------

def test_no_paho_means_no_publisher(monkeypatch):
    monkeypatch.setattr(obs, "_mqtt", None)

    assert obs.make_local_publisher() is None


def test_the_factory_connects_to_loopback_and_starts_the_network_loop(monkeypatch):
    calls = []

    class _Client:
        def __init__(self, api, client_id):
            calls.append(("init", client_id))

        def connect_async(self, host, port, keepalive):
            calls.append(("connect_async", host, port))

        def loop_start(self):
            calls.append(("loop_start",))

    monkeypatch.setattr(obs, "_mqtt", SimpleNamespace(
        Client=_Client, CallbackAPIVersion=SimpleNamespace(VERSION2=2)))

    assert obs.make_local_publisher() is not None
    assert ("connect_async", "127.0.0.1", 1883) in calls
    assert ("loop_start",) in calls


def test_a_publish_result_without_an_rc_is_a_failure_not_a_success():
    class _NoRc:
        def publish(self, *a, **k):
            return object()

    with pytest.raises(ConnectionError):
        obs.LocalBrokerPublisher(_NoRc()).publish("t", {"raw": "11"})


# ===========================================================================
# v3 (Fable r3): ONE predicate for state AND behaviour.
# Spec: ~/.claude/plans/cs1_r1_review_2026_11_18/STATE_TABLE_v3.md
# A session's callback PUBLISHES iff _observer_state_for(that session) is a
# runtime state; otherwise it counts dropped_not_live[<state>] and publishes
# nothing. The snapshot is _observer_state_for(self._meshcore).
# ===========================================================================

import asyncio  # noqa: E402

from utils import tx_guard  # noqa: E402

CURRENT = ("wired", "stale_unwired_new", "stale_wired_new", "none")


def _cb_of(mc):
    (_, cb), = [s for s in mc.subscribed if s[0] == _EventType.RX_LOG_DATA]
    return cb


def _scenario(enabled, connected, current):
    """Wire on mc1, then move the world; return (handler, mc1 callback, pub, mc2)."""
    pub = _FakePublisher()
    h, mc1 = _on(pub=pub)
    cb1 = _cb_of(mc1)
    mc2 = _FakeMeshCore()
    if current == "stale_unwired_new":
        h._meshcore = mc2
    elif current == "stale_wired_new":
        h._meshcore = mc2
        h._init_observer(_EventType, publisher=pub)
    elif current == "none":
        h._meshcore = None
    h._connected = connected
    h.config.meshcore.observer_enabled = enabled
    return h, cb1, pub, mc2


@pytest.mark.parametrize("enabled, connected, current",
                         list(itertools.product((True, False), (True, False), CURRENT)),
                         ids=lambda v: str(v))
def test_a_callback_publishes_exactly_when_its_sessions_state_is_live(enabled, connected, current):
    h, cb1, pub, _ = _scenario(enabled, connected, current)

    asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))

    live = enabled and connected and current == "wired"
    assert len(pub.sent) == (1 if live else 0)
    if not live:   # dropped BEFORE translation: not a refusal, not an outcome
        w = h._observer_witness.snapshot()
        assert (w["refused"], w["queued"], w["publish_failed"]) == ({}, 0, 0)
    # the snapshot and the behaviour come from the same predicate
    if current == "wired":
        assert (h.observer_snapshot()["state"] in obs.RUNTIME_STATES) == live


def test_f1_connected_false_with_the_session_lingering_publishes_nothing_and_counts():
    # Fable r3 F1 shape: _connected False, session never closed.
    h, cb1, pub, _ = _scenario(True, False, "wired")
    for _ in range(5):
        asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))

    assert pub.sent == []
    assert h.observer_snapshot() == {"state": "no_radio",
                                     "dropped_not_live": {"no_radio": 5}}


def test_f1_a_leaked_old_session_never_double_publishes_under_the_new_key():
    h, cb1, pub, mc2 = _scenario(True, True, "stale_wired_new")
    frame = _event_payload()
    asyncio.run(cb1(SimpleNamespace(payload=frame)))           # old session hears it
    asyncio.run(_cb_of(mc2)(SimpleNamespace(payload=frame)))   # new session hears it

    assert len(pub.sent) == 1


def test_f4_a_live_disable_stops_publishing_at_once():
    h, cb1, pub, _ = _scenario(True, True, "wired")
    asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))
    h.config.meshcore.observer_enabled = False
    for _ in range(10):
        asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))

    assert len(pub.sent) == 1


def test_a_dropped_packet_is_not_an_outcome():
    h, cb1, pub, _ = _scenario(True, True, "wired")
    asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))
    h._connected = False
    asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))
    h._connected = True

    snap = h.observer_snapshot()
    assert (snap["state"], snap["last_outcome_run"], snap["queued"]) == ("ok", 1, 1)
    assert snap["dropped_not_live"] == {"no_radio": 1}


# --- the REAL _connect path, fake meshcore module (no radio, no network) ---

class _Session(_FakeMeshCore):
    fetch_error = None

    async def start_auto_message_fetching(self):
        if _Session.fetch_error is not None:
            raise _Session.fetch_error

    async def disconnect(self):
        pass


def _real_handler(monkeypatch, sim=False):
    import contextlib
    import gateway.meshcore_handler as hm

    class _ET(_EventType):
        CONTACT_MSG_RECV, CHANNEL_MSG_RECV, ADVERTISEMENT = "c", "ch", "ad"
        ACK, NEW_CONTACT = "ack", "nc"

    class _MC:
        @staticmethod
        async def create_tcp(host, port):
            return _Session()

    @contextlib.contextmanager
    def _lock(**kw):
        yield True

    monkeypatch.setattr(hm, "_meshcore_mod", SimpleNamespace(MeshCore=_MC, EventType=_ET))
    monkeypatch.setattr(hm, "_HAS_MESHCORE", True)
    monkeypatch.setattr(hm, "acquire_for_connect", _lock)
    monkeypatch.setattr(hm, "_get_meshcore_connection_manager", lambda: SimpleNamespace(
        register_persistent=lambda *a, **k: None, unregister_persistent=lambda: None))
    monkeypatch.setattr(mixin, "make_local_publisher",
                        lambda: (_ for _ in ()).throw(AssertionError("no real client")))
    _Session.fetch_error = None
    h = _bare_handler(enabled=True)
    h._meshcore, h._connected, h._loop = None, False, None
    h._simulation_mode = sim
    h.config.meshcore.connection_type = "tcp"
    h.config.meshcore.tcp_host, h.config.meshcore.tcp_port = "127.0.0.1", 1
    h.config.meshcore.auto_fetch_messages = True
    h.config.meshcore.repliable_contacts = []

    async def _noop(*a, **k):
        pass
    h._refresh_radio_state = _noop
    h._sync_clock_on_connect = _noop
    h._apply_desired_and_log_drift = lambda: None
    h._start_advert_heartbeat = lambda: None
    h._observer_publisher = _FakePublisher()
    return h


def test_real_connect_with_a_fetch_error_after_wiring_publishes_nothing(monkeypatch):
    # Fable r3 F1 section C, through the real MeshCoreHandler._connect.
    h = _real_handler(monkeypatch)
    _Session.fetch_error = RuntimeError("fetch boom")
    with tx_guard.allow_meshcore_egress():   # mock radio, not RF
        asyncio.run(h._connect())
    # since the handler teardown fix (test_meshcore_handler_teardown.py) the
    # session is CLOSED and dropped; its callback may still fire once more
    # from a library that has not yet stopped dispatching — it must not publish
    old = h._observer_setup[0]
    assert h._connected is False and h._meshcore is None and old is not None

    for _ in range(5):
        asyncio.run(_cb_of(old)(SimpleNamespace(payload=_event_payload())))

    assert h._observer_publisher.sent == []
    # v4 (r4 L2): a session the handler moved past is `stale_session`, never
    # mixed into the `no_radio` residual
    assert h.observer_snapshot() == {"state": "no_radio",
                                     "dropped_not_live": {"stale_session": 5}}


def test_real_connect_happy_path_publishes(monkeypatch):
    # the control for the test above: same harness, no fault -> live.
    h = _real_handler(monkeypatch)
    with tx_guard.allow_meshcore_egress():   # mock radio, not RF
        asyncio.run(h._connect())
    asyncio.run(_cb_of(h._meshcore)(SimpleNamespace(payload=_event_payload())))

    assert len(h._observer_publisher.sent) == 1
    assert h.observer_snapshot()["state"] == "ok"


def test_f2_simulation_mode_with_the_observer_on_warns(monkeypatch, caplog):
    import gateway.meshcore_handler as hm

    class _Sim(_FakeMeshCore):
        async def start(self):
            pass
    monkeypatch.setattr(hm, "MeshCoreSimulator", _Sim)
    h = _real_handler(monkeypatch, sim=True)
    with caplog.at_level("WARNING"), tx_guard.allow_meshcore_egress():
        asyncio.run(h._connect())

    assert h.observer_snapshot()["state"] == "not_subscribed"
    assert _warnings(caplog, "simulation")


def test_simulation_mode_with_the_observer_off_is_quiet(monkeypatch, caplog):
    # mutant x20.
    import gateway.meshcore_handler as hm

    class _Sim(_FakeMeshCore):
        async def start(self):
            pass
    monkeypatch.setattr(hm, "MeshCoreSimulator", _Sim)
    h = _real_handler(monkeypatch, sim=True)
    h.config.meshcore.observer_enabled = False
    with caplog.at_level("WARNING"), tx_guard.allow_meshcore_egress():
        asyncio.run(h._connect())

    assert not _warnings(caplog, "observer")


# --- F3: rate-limited, and every line not emitted is COUNTED ---------------

def test_f3_interleaved_outcomes_log_once_per_kind_per_window(caplog, monkeypatch):
    h, mc, clock = _on_with_clock(monkeypatch)
    good = mc.self_info
    with caplog.at_level("INFO"):
        for i in range(200):
            mc.self_info = good if i % 2 == 0 else {}
            _fire(mc, _event_payload())
    infos = [r for r in caplog.records if r.levelname == "INFO" and "recover" in r.getMessage().lower()]

    assert len(_warnings(caplog, "refus")) == 1
    assert len(infos) == 1
    # v4: the journal follows STATE. After "recovered" is logged, each return
    # to ok matches the journal (no line owed); each of the 99 later entries
    # into refusing is a change NOT emitted -> counted, none silent.
    assert h.observer_snapshot()["log_lines_suppressed"] == 99


def test_f3_the_window_reopens_after_300s(caplog, monkeypatch):
    pub = _FakePublisher(error=ConnectionError("down"))
    h, mc, clock = _on_with_clock(monkeypatch, pub=pub)
    with caplog.at_level("WARNING"):
        _fire(mc, _event_payload())
        pub.error = None
        _fire(mc, _event_payload())
        pub.error = ConnectionError("down")
        _fire(mc, _event_payload())          # suppressed: inside the window
        clock.t += 299
        pub.error = None
        _fire(mc, _event_payload())
        pub.error = ConnectionError("down")
        _fire(mc, _event_payload())          # 299 s: STILL inside (mutant x13)
        assert len(_warnings(caplog, "fail")) == 1
        clock.t += 2
        pub.error = None
        _fire(mc, _event_payload())
        pub.error = ConnectionError("down")
        _fire(mc, _event_payload())          # window reopened

    assert len(_warnings(caplog, "fail")) == 2


def test_the_first_packet_ever_never_logs_recovered(caplog):
    # Fable r3 m15.
    with caplog.at_level("INFO"):
        h, mc = _on()
        _fire(mc, _event_payload())

    assert not [r for r in caplog.records if "recover" in r.getMessage().lower()]


# --- F5: the r3 survivors --------------------------------------------------

@pytest.mark.parametrize("error", [RuntimeError("x"), ValueError("x"), TypeError("x"), OSError("x")],
                         ids=lambda e: type(e).__name__)
def test_any_publish_exception_is_counted_never_raised_into_the_radio_loop(error):
    # r3 m31: only ConnectionError was tested.
    h, mc = _on(pub=_FakePublisher(error=error))
    _fire(mc, _event_payload())                        # must not raise

    assert h.observer_snapshot()["state"] == "failing"


@pytest.mark.parametrize("error", [RuntimeError("x"), OSError("x"), ValueError("x")],
                         ids=lambda e: type(e).__name__)
def test_any_factory_exception_reads_client_error(error, monkeypatch):
    # r3 m33: only AttributeError was tested.
    monkeypatch.setattr(mixin, "make_local_publisher",
                        lambda: (_ for _ in ()).throw(error))
    h = _bare_handler(enabled=True)
    h._init_observer(_EventType)                       # must not raise

    assert h.observer_snapshot()["state"] == "client_error"


def test_the_witness_ages_on_the_monotonic_clock_not_wall_time(monkeypatch):
    # r3 m36 (hfm #6: wall time is forgeable on RTC-less Pis).
    h, mc, clock = _on_with_clock(monkeypatch)
    monkeypatch.setattr(mixin.time, "time", lambda: 0.0)
    _fire(mc, _event_payload())
    clock.t += 42

    assert h.observer_snapshot()["last_queued_age_s"] == 42


def test_a_setup_fault_with_no_session_never_leaks_its_error_onto_no_radio(monkeypatch):
    # r3 m23: a record taken with session None matched a later None.
    monkeypatch.setattr(mixin, "make_local_publisher",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    h = _bare_handler(enabled=True)
    h._meshcore = None
    h._init_observer(_EventType)

    assert h.observer_snapshot() == {"state": "no_radio"}


def test_the_enabled_and_disabled_lines_are_info(caplog):
    # r3 m29 m30: the only positive line an operator gets.
    with caplog.at_level("INFO"):
        h, mc = _on()
        h.config.meshcore.observer_enabled = False
        h._init_observer(_EventType)
    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]

    assert any("observer enabled" in m for m in infos)
    assert any("observer disabled" in m for m in infos)


def test_an_internal_errors_last_error_says_internal(monkeypatch):
    # r3 o37.
    h, mc = _on()
    monkeypatch.setattr(mixin, "build_packet_message",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")))
    _fire(mc, _event_payload())

    assert h.observer_snapshot()["last_error"].startswith("internal")


# ===========================================================================
# v4 (Fable r4): the JOURNAL is a projection of the DERIVED STATE.
# Spec: ~/.claude/plans/cs1_r1_review_2026_11_18/STATE_TABLE_v4.md
# A line is owed whenever the current (state, detail) differs from the last
# LOGGED one; rate-limited per key; a suppressed change is counted ONCE and
# emitted ("still ...") on the first packet after its window reopens — so the
# journal can disagree with the state for at most one window, never forever.
# ===========================================================================

def _observer_records(caplog):
    return [r for r in caplog.records if "observer" in r.getMessage().lower()]


def test_m1_the_journal_never_ends_on_recovered_while_failing(caplog, monkeypatch):
    # Fable r4 M1: fail, recover 5 s later, fail again 5 s later, stay down
    # a day: the last line was "recovered" forever.
    pub = _FakePublisher(error=ConnectionError("rc=4"))
    h, mc, clock = _on_with_clock(monkeypatch, pub=pub)
    with caplog.at_level("INFO"):
        _fire(mc, _event_payload())
        clock.t += 5
        pub.error = None
        _fire(mc, _event_payload())
        clock.t += 5
        pub.error = ConnectionError("rc=4")
        _fire(mc, _event_payload())                   # inside the window: owed
        clock.t += 86400
        _fire(mc, _event_payload())                   # window reopened: emitted

    last = _observer_records(caplog)[-1]
    assert last.levelname == "WARNING" and "fail" in last.getMessage().lower()
    assert h.observer_snapshot()["state"] == "failing"


def test_i1_a_new_refusal_reason_inside_one_run_is_logged(caplog):
    # Fable r4 I1: one WARNING named only the first reason.
    h, mc = _on()
    with caplog.at_level("WARNING"):
        mc.self_info = {}
        _fire(mc, _event_payload())                   # bad_observer_key
        mc.self_info = {"public_key": KEY.lower(), "name": "MA"}
        _fire(mc, {**_event_payload(), "recv_time": True})   # bad_recv_time

    msgs = [r.getMessage() for r in _warnings(caplog, "refus")]
    assert any("bad_observer_key" in m for m in msgs)
    assert any("bad_recv_time" in m for m in msgs)


def test_l1_a_current_session_not_live_drop_is_logged_with_its_count(caplog):
    # Fable r4 L1: drops from a live, unclosed session logged nothing.
    h, mc = _on()
    _fire(mc, _event_payload())
    h._connected = False
    with caplog.at_level("WARNING"):
        for _ in range(3):
            _fire(mc, _event_payload())

    msgs = [r.getMessage() for r in _warnings(caplog, "no_radio")]
    assert len(msgs) == 1 and "drop" in msgs[0].lower()


def test_l2_a_stale_sessions_drops_are_keyed_stale_not_no_radio():
    # Fable r4 L2: a stale session's drop was labelled no_radio while the
    # radio was UP, mixing it into the measured residual.
    h, cb1, pub, mc2 = _scenario(True, True, "stale_wired_new")
    asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))

    snap = h.observer_snapshot()
    assert snap["state"] == "never"                   # the CURRENT session is fine
    assert snap["dropped_not_live"] == {"stale_session": 1}


def test_l2_the_current_session_not_connected_is_the_no_radio_key():
    h, cb1, pub, _ = _scenario(True, False, "wired")
    asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))

    assert h.observer_snapshot()["dropped_not_live"] == {"no_radio": 1}


def test_a_live_disable_drop_is_keyed_disabled():
    # r4 a06: every drop counted under one fixed key went unseen.
    h, cb1, pub, _ = _scenario(False, True, "wired")
    asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))

    assert h._observer_witness.dropped() == {"disabled": 1}


def test_bad_to_other_bad_is_logged(caplog):
    # r4 b13: refusing -> failing without passing through ok.
    pub = _FakePublisher(error=ConnectionError("down"))
    h, mc = _on(pub=pub)
    with caplog.at_level("WARNING"):
        mc.self_info = {}
        _fire(mc, _event_payload())                   # refusing
        mc.self_info = {"public_key": KEY.lower(), "name": "MA"}
        _fire(mc, _event_payload())                   # failing

    assert _warnings(caplog, "refus") and _warnings(caplog, "fail")


def test_the_warning_names_its_detail(caplog):
    # r4 e11.
    with caplog.at_level("WARNING"):
        h, mc = _on(iata="ito")
        _fire(mc, _event_payload())

    assert any("bad_iata" in r.getMessage() for r in _warnings(caplog, "refus"))


# (r4 b03/b04 window pins moved to test_r5_the_window_rules_hold_through_the_journal_itself:
#  allow_log had no src caller and was removed in v5.)


def test_a_setup_error_from_another_session_is_never_shown():
    # r4 c04.
    mc = _FakeMeshCore()
    mc.subscribe = None
    h = _bare_handler(enabled=True, mc=mc)
    h._init_observer(_EventType, publisher=_FakePublisher())
    assert "last_error" in h.observer_snapshot()

    h._meshcore = _FakeMeshCore()                     # new session, not wired yet

    assert h.observer_snapshot() == {"state": "not_subscribed"}


def test_a_snapshot_is_a_copy_never_the_live_counter():
    # r4 c08.
    h, cb1, pub, _ = _scenario(True, False, "wired")
    asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))
    snap = h.observer_snapshot()
    snap["dropped_not_live"]["no_radio"] = 999

    assert h.observer_snapshot()["dropped_not_live"] == {"no_radio": 1}


def test_a_pending_change_is_counted_once_however_many_packets(caplog, monkeypatch):
    # v4 mutant j04: one held-back change is ONE suppressed line.
    pub = _FakePublisher(error=ConnectionError("rc=4"))
    h, mc, clock = _on_with_clock(monkeypatch, pub=pub)
    _fire(mc, _event_payload())                   # failing: logged
    pub.error = None
    _fire(mc, _event_payload())                   # recovered: logged
    pub.error = ConnectionError("rc=4")
    for _ in range(10):
        clock.t += 1
        _fire(mc, _event_payload())               # failing again, inside the window

    assert h.observer_snapshot()["log_lines_suppressed"] == 1


def test_disabled_is_the_drop_cause_even_on_a_stale_session():
    # v4 mutant k02: `disabled` is checked before `stale_session`.
    h, cb1, pub, _ = _scenario(False, True, "stale_unwired_new")
    asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))

    assert h._observer_witness.dropped() == {"disabled": 1}


# ===========================================================================
# v5 (Fable r5): init lines ARE journal lines; counts tell the truth; reset
# is total. Spec: STATE_TABLE_v4.md (+ v5 notes in R5_FINDINGS.md).
# ===========================================================================

def test_r5_med1_a_reconnect_never_leaves_enabled_as_the_last_word_while_failing(
        caplog, monkeypatch):
    # Fable r5 MED-1: the init "enabled" line never updated the logged key,
    # so after a reconnect the journal ended on it while failing for 25 h.
    pub = _FakePublisher(error=ConnectionError("rc=4"))
    h, mc, clock = _on_with_clock(monkeypatch, pub=pub)
    with caplog.at_level("INFO"):
        _fire(mc, _event_payload())                   # failing: WARNING
        clock.t += 10
        mc2 = _FakeMeshCore()                         # the radio reconnects
        h._meshcore = mc2
        h._init_observer(_EventType, publisher=pub)   # "enabled" INFO
        for _ in range(3):
            clock.t += 300
            _fire(mc2, _event_payload())              # still failing

    last = _observer_records(caplog)[-1]
    assert last.levelname == "WARNING" and "fail" in last.getMessage().lower()


def test_r5_low1_a_re_enable_on_the_same_session_says_so_and_rereads_the_iata(caplog):
    pub = _FakePublisher()
    h, mc = _on(pub=pub)
    h.config.meshcore.observer_enabled = False
    h._init_observer(_EventType)
    h.config.meshcore.observer_enabled = True
    h.config.meshcore.observer_iata = "HNL"
    with caplog.at_level("INFO"):
        h._init_observer(_EventType, publisher=pub)
    _fire(mc, _event_payload())

    assert _observer_records(caplog)[-1].getMessage().lower().count("enabled") == 1
    assert pub.sent[-1][0].startswith("meshcore/HNL/")


def test_r5_low3_the_not_live_line_counts_every_drop_it_reports(caplog):
    # r5 LOW-3: after a teardown (current session None) a late callback from
    # the old session is counted `stale_session` but the no_radio line said
    # "(0 so far)": the line looked up dropped[state], not the drops it reports.
    h, cb1, pub, _ = _scenario(True, True, "none")
    with caplog.at_level("WARNING"):
        asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))

    msg = _warnings(caplog, "no_radio")[0].getMessage()
    assert "(1 so far)" in msg


def test_r5_the_disabled_drop_line_is_info(caplog):
    # r5 k08.
    h, cb1, pub, _ = _scenario(False, True, "wired")
    with caplog.at_level("INFO"):
        asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))

    lines = [r for r in caplog.records if "dropping every packet" in r.getMessage()]
    assert [r.levelname for r in lines] == ["INFO"]
    assert "(1 so far)" in lines[0].getMessage()


@pytest.mark.parametrize("second, expect_lines", [
    (ConnectionError("rc=7"), 1),          # same class, other text: ONE line
    (TimeoutError("slow"), 2),             # other class: a new line
], ids=["same-class", "other-class"])
def test_r5_the_failing_key_is_outcome_plus_error_class(second, expect_lines, caplog):
    # r5 k04 k05 k06: per-message keys would log every distinct error text.
    pub = _FakePublisher(error=ConnectionError("rc=4"))
    h, mc = _on(pub=pub)
    with caplog.at_level("WARNING"):
        _fire(mc, _event_payload())
        pub.error = second
        _fire(mc, _event_payload())

    assert len(_warnings(caplog, "fail")) == expect_lines


def test_r5_publish_failing_and_internal_error_are_different_keys(caplog, monkeypatch):
    pub = _FakePublisher(error=RuntimeError("x"))
    h, mc = _on(pub=pub)
    with caplog.at_level("WARNING"):
        _fire(mc, _event_payload())                       # publish_failed:RuntimeError
        monkeypatch.setattr(mixin, "build_packet_message",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        _fire(mc, _event_payload())                       # internal_error:RuntimeError

    assert _warnings(caplog, "fail") and _warnings(caplog, "internal")


def test_r5_reset_is_total_every_field_back_to_a_fresh_witness():
    # r5 j09-j16: only "reset is a no-op" was killed.
    clock = _FakeMonotonic(1000.0)
    w = obs.ObserverWitness(clock=clock)
    import copy
    fresh = copy.deepcopy({k: v for k, v in vars(w).items() if k not in ("_clock", "_lock")})
    msg = obs.build_packet_message(_event_payload(), iata="ITO", observer_key=KEY)
    w.record_queued(msg)
    w.record_refused(obs.Refused("bad_iata"))
    w.record_publish_failed(msg, ConnectionError("x"))
    w.record_dropped("no_radio")
    w.journal_owes("failing:x")
    w.journal_owes("failing:x")
    clock.t += 1
    w.journal_owes("refusing:y")
    clock.t += 1
    w.journal_owes("failing:x")          # held back: pending + suppressed (r5 j11 j12)
    assert w.snapshot()["log_lines_suppressed"] == 1

    w.reset()

    after = {k: v for k, v in vars(w).items() if k not in ("_clock", "_lock")}
    assert after == fresh


def test_r5_the_window_rules_hold_through_the_journal_itself():
    # allow_log is gone (no src caller); the window is pinned where it lives.
    clock = _FakeMonotonic(1000.0)
    w = obs.ObserverWitness(clock=clock)
    assert w.journal_owes("a:") is True
    assert w.journal_owes("b:") is True
    clock.t += 299.999
    assert w.journal_owes("a:") is False              # inside: held back, counted
    clock.t += 0.001
    assert w.journal_owes("a:") is True               # 300.0 exactly: open
    clock.t = 900.0                                   # before b's stamp (1000): backward
    assert w.journal_owes("b:") is True               # clock went backward: open


def test_r5_a_disable_line_is_the_journals_statement_no_duplicate_per_packet(caplog):
    # v5 mutant n02: the init "disabled" line already said it.
    h, mc = _on()
    with caplog.at_level("INFO"):
        h.config.meshcore.observer_enabled = False
        h._init_observer(_EventType)
        for _ in range(3):
            _fire(mc, _event_payload())

    assert not [r for r in caplog.records if "dropping every packet" in r.getMessage()]
    assert h._observer_witness.dropped() == {"disabled": 3}


def test_r5_an_init_line_clears_a_pending_change_so_the_next_is_counted(monkeypatch):
    # v5 mutant n06: a pending key that survived the init line hid the next
    # held-back change from log_lines_suppressed.
    pub = _FakePublisher(error=ConnectionError("rc=4"))
    h, mc, clock = _on_with_clock(monkeypatch, pub=pub)
    _fire(mc, _event_payload())                  # failing: logged
    pub.error = None
    _fire(mc, _event_payload())                  # recovered: logged
    pub.error = ConnectionError("rc=4")
    _fire(mc, _event_payload())                  # failing again: held back (1)
    mc2 = _FakeMeshCore()
    h._meshcore = mc2
    h._init_observer(_EventType, publisher=pub)  # the "enabled" line
    _fire(mc2, _event_payload())                 # failing, still inside: held back (2)

    assert h.observer_snapshot()["log_lines_suppressed"] == 2


# ===========================================================================
# Fable r6 (0 H / 0 M / 2 L / 6 I, no behavioural defect): pin the gaps.
# ===========================================================================

def test_r6_a_re_enable_on_a_wired_session_records_the_healthy_key(caplog):
    # r6 n05 (no journal_logged on the no-op) / n15 (it records DISABLED):
    # the "enabled (already wired)" line IS the journal's statement, so the
    # next good packet owes no "recovered" line.
    pub = _FakePublisher()
    h, mc = _on(pub=pub)
    h.config.meshcore.observer_enabled = False
    h._init_observer(_EventType)
    h.config.meshcore.observer_enabled = True
    h._init_observer(_EventType, publisher=pub)
    with caplog.at_level("INFO"):
        _fire(mc, _event_payload())

    assert not [r for r in caplog.records if "recover" in r.getMessage().lower()]


def test_r6_the_not_live_line_counts_drops_of_every_cause(caplog):
    # r6 n11 (max instead of sum): 2 `disabled` + 1 `no_radio` = 3.
    h, cb1, pub, _ = _scenario(False, True, "wired")
    asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))
    asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))
    h.config.meshcore.observer_enabled = True
    h._connected = False
    with caplog.at_level("WARNING"):
        asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))

    assert "(3 so far)" in _warnings(caplog, "no_radio")[0].getMessage()


def test_r6_the_disabled_line_counts_drops_of_every_cause(caplog):
    # r6 n12 (the disabled line counted only `disabled` drops): 1 + 1 = 2.
    h, cb1, pub, _ = _scenario(True, False, "wired")
    asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))     # no_radio
    h._connected = True
    h.config.meshcore.observer_enabled = False
    with caplog.at_level("INFO"):
        asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))  # disabled

    line = [r.getMessage() for r in caplog.records if "dropping every packet" in r.getMessage()][0]
    assert "(2 so far)" in line


def test_r6_a_stale_sessions_drop_never_speaks_for_a_healthy_current_session(caplog):
    # r6 k25: the journal projects the CURRENT session (accepted r5 LOW-2).
    h, cb1, pub, _ = _scenario(True, True, "stale_wired_new")
    with caplog.at_level("WARNING"):
        asyncio.run(cb1(SimpleNamespace(payload=_event_payload())))

    assert not _warnings(caplog, "observer")
    assert h.observer_snapshot()["dropped_not_live"] == {"stale_session": 1}


def test_r6_reset_also_clears_internal_errors_and_the_ceiling_counter():
    # r6 j16f j16i: the reset-is-total test never set these two.
    w = obs.ObserverWitness(clock=_FakeMonotonic())
    big = obs.build_packet_message(_event_payload(packet=bytes(174)), iata="ITO", observer_key=KEY)
    w.record_queued(big)                                # over the 173 B ceiling
    w.record_internal_error(RuntimeError("bug"))
    assert (w.snapshot()["ceiling_contradicted"], w.snapshot()["internal_errors"]) == (1, 1)

    w.reset()

    assert (w.snapshot()["ceiling_contradicted"], w.snapshot()["internal_errors"]) == (0, 0)


def test_r6_last_error_persists_through_a_refusal_until_a_queued_packet():
    # r6 j23: the accepted r5 behaviour, pinned.
    pub = _FakePublisher(error=ConnectionError("rc=4"))
    h, mc = _on(pub=pub)
    _fire(mc, _event_payload())                         # failing
    mc.self_info = {}
    _fire(mc, _event_payload())                         # refusing

    snap = h.observer_snapshot()
    assert snap["state"] == "refusing" and "rc=4" in snap["last_error"]


def test_r6_an_exception_whose_str_raises_never_escapes_into_the_radio_loop():
    # r6 INFO-4: the f-string in record_publish_failed called str(error).
    class _Unprintable(Exception):
        def __str__(self):
            raise RuntimeError("str() raised")
    h, mc = _on(pub=_FakePublisher(error=_Unprintable()))

    _fire(mc, _event_payload())                         # must not raise

    snap = h.observer_snapshot()
    assert (snap["state"], snap["publish_failed"], snap["last_outcome"]) == (
        "failing", 1, "publish_failed")
    assert snap["last_error"].startswith("_Unprintable")
