"""MeshCore handler: a lost session is torn down ONE way, so it can reconnect.

Found by the CS-1 observer non-author review r4 (2026-11-19, Fable): after
either loop-exception path in ``_async_run`` the persistent-owner registration
was never dropped, and ``acquire_for_connect`` refuses every connect while ANY
persistent owner is registered — including the handler's own
``gateway-bridge``. Result: the handler never reconnected until the process
restarted (MeshCore messaging down). The generic-exception path also left the
session OPEN (``_connected=False`` only), and a fetch error after subscribing
in ``_connect`` left it open too.

Contract pinned here (real ``_connect`` / ``_handle_disconnection`` /
``_async_run``, REAL connection manager, fake meshcore module):
after any lost session — the session is closed, ``_meshcore`` is None,
``_subscriptions`` is empty, our persistent registration is gone (and ONLY
ours: a foreign owner is never unregistered), and the next connect succeeds.
"""

import asyncio
import contextlib
import threading
from types import SimpleNamespace

import pytest

import gateway.meshcore_handler as hm
import utils.meshcore_connection as mconn
from gateway.meshcore_handler import MeshCoreHandler
from utils import tx_guard


def _bare_handler():
    """A handler shell without __init__ (no radio, no threads)."""
    h = MeshCoreHandler.__new__(MeshCoreHandler)
    h.stats = {}
    h._stats_lock = threading.Lock()
    h._subscriptions = []
    h.config = SimpleNamespace(meshcore=SimpleNamespace())
    return h


class _ET:
    CONTACT_MSG_RECV, CHANNEL_MSG_RECV, ADVERTISEMENT = "c", "ch", "ad"
    ACK, NEW_CONTACT, RX_LOG_DATA = "ack", "nc", "rx"


class _Session:
    made = []
    fetch_error = None

    def __init__(self):
        _Session.made.append(self)
        self.closed = False
        self.self_info = {"public_key": "AB" * 32, "name": "ma"}

    def subscribe(self, event_type, cb):
        return object()

    async def start_auto_message_fetching(self):
        if _Session.fetch_error is not None:
            raise _Session.fetch_error

    async def disconnect(self):
        self.closed = True


class _MC:
    @staticmethod
    async def create_tcp(host, port):
        return _Session()


class _FastReconnect:
    attempts = 0
    config = SimpleNamespace(max_delay=0.01)

    def should_retry(self):
        return True

    def record_success(self):
        pass

    def record_failure(self):
        pass

    def reset(self):
        pass

    def get_delay(self):
        return 0.01


@pytest.fixture
def manager():
    """The REAL connection manager, clean before and after (process-wide singleton)."""
    def _clean():
        mconn.reset_connection_manager()
        while mconn.MESHCORE_CONNECTION_LOCK.locked():
            try:
                mconn.MESHCORE_CONNECTION_LOCK.release()
            except RuntimeError:
                break
    _clean()
    yield mconn.get_connection_manager()
    _clean()


@pytest.fixture
def handler(monkeypatch, manager):
    monkeypatch.setattr(hm, "_meshcore_mod", SimpleNamespace(MeshCore=_MC, EventType=_ET))
    monkeypatch.setattr(hm, "_HAS_MESHCORE", True)
    monkeypatch.setattr(mconn, "wait_for_cooldown", lambda *a, **k: None)
    _Session.made, _Session.fetch_error = [], None
    h = _bare_handler()
    h._meshcore, h._connected, h._loop = None, False, None
    h._simulation_mode = False
    h._stop_event = threading.Event()
    cfg = h.config.meshcore
    cfg.connection_type, cfg.tcp_host, cfg.tcp_port = "tcp", "127.0.0.1", 1
    cfg.auto_fetch_messages, cfg.repliable_contacts = True, []

    async def _noop(*a, **k):
        pass
    h._refresh_radio_state = _noop
    h._sync_clock_on_connect = _noop
    h._apply_desired_and_log_drift = lambda: None
    h._start_advert_heartbeat = lambda: None
    h._cancel_advert_heartbeat = lambda: None
    h._process_outbound = _noop
    h._poll_channel_messages = _noop
    h._notify_status = lambda s: None
    h.health = SimpleNamespace(record_connection_event=lambda *a, **k: None,
                               record_error=lambda *a, **k: "test")
    h._inbound_policy = SimpleNamespace(log_posture=lambda h: None)
    h._reconnect = _FastReconnect()
    return h


def _connect(h):
    async def _go():
        h._loop = asyncio.get_running_loop()
        await h._connect()
    with tx_guard.allow_meshcore_egress():   # mock radio, not RF
        asyncio.run(_go())


def _torn_down(h, session, manager):
    return (session.closed, h._meshcore, h._subscriptions, h._connected,
            manager.has_persistent()) == (True, None, [], False, False)


def test_a_clean_connect_registers_us_as_the_persistent_owner(handler, manager):
    # control: the harness reaches the real register path
    _connect(handler)

    assert handler._connected is True
    assert manager.get_meshcore() is _Session.made[0]


def test_handle_disconnection_tears_down_and_the_next_connect_succeeds(handler, manager):
    # the OSError / ConnectionError branch of _async_run calls this
    _connect(handler)
    s1 = _Session.made[0]

    asyncio.run(handler._handle_disconnection("serial gone"))

    assert _torn_down(handler, s1, manager)
    _connect(handler)
    assert handler._connected is True
    assert handler._meshcore is _Session.made[1]


def test_a_fetch_error_after_subscribe_closes_the_session_it_opened(handler, manager):
    # r3 F1 section C: the session stayed OPEN with _meshcore still set
    _Session.fetch_error = RuntimeError("fetch boom")
    _connect(handler)
    s1 = _Session.made[0]

    assert _torn_down(handler, s1, manager)
    _Session.fetch_error = None
    _connect(handler)
    assert handler._connected is True


@pytest.mark.parametrize("error", [RuntimeError("outbound bug"), OSError("serial gone")],
                         ids=["generic-exception", "oserror"])
def test_after_a_loop_exception_the_handler_really_reconnects(error, handler, manager):
    # r4 OOS-1: both branches of _async_run; before the fix every later
    # connect was refused ("persistent owner 'gateway-bridge' already active")
    calls = {"n": 0}

    async def _outbound():
        calls["n"] += 1
        if calls["n"] == 1:
            raise error
        if len(_Session.made) >= 2 and handler._connected:
            handler._stop_event.set()
    handler._process_outbound = _outbound

    async def _run():
        handler._loop = asyncio.get_running_loop()
        await asyncio.wait_for(handler._async_run(), timeout=5)
    with tx_guard.allow_meshcore_egress():   # mock radio, not RF
        asyncio.run(_run())

    s1, s2 = _Session.made[0], _Session.made[1]
    assert s1.closed is True                 # the lost session was CLOSED
    assert handler._meshcore is s2           # a NEW session is live
    assert manager.get_meshcore() is s2      # and registered as the owner


def test_a_failing_unregister_is_loud_and_the_teardown_still_completes(handler, manager,
                                                                      monkeypatch, caplog):
    # a stuck registration blocks EVERY reconnect: it must never be silent
    _connect(handler)
    s1 = _Session.made[0]
    monkeypatch.setattr(manager, "unregister_persistent",
                        lambda: (_ for _ in ()).throw(RuntimeError("stuck")))

    with caplog.at_level("WARNING"):
        asyncio.run(handler._handle_disconnection("x"))

    assert (s1.closed, handler._meshcore, handler._connected) == (True, None, False)
    assert any(r.levelname == "WARNING" and "unregister" in r.getMessage()
               for r in caplog.records)


def test_teardown_never_unregisters_someone_elses_link(handler, manager):
    # composable: a persistent link we did not register is not ours to drop
    foreign = object()
    loop = asyncio.new_event_loop()
    try:
        manager.register_persistent(foreign, loop, owner="someone-else")
        handler._meshcore = _Session()
        handler._connected = True

        asyncio.run(handler._handle_disconnection("x"))

        assert manager.get_meshcore() is foreign
    finally:
        loop.close()


# ===========================================================================
# Teardown review r1 (Fable, 2026-11-19): 0 H / 0 M / 3 L / 5 I.
# ===========================================================================

def test_a_foreign_link_under_our_owner_name_is_still_not_ours(handler, manager):
    # r1 M19: identity, never the owner NAME, decides what is ours.
    impostor = object()
    loop = asyncio.new_event_loop()
    try:
        manager.register_persistent(impostor, loop, owner="gateway-bridge")
        handler._meshcore = _Session()
        handler._connected = True

        asyncio.run(handler._handle_disconnection("x"))

        assert manager.get_meshcore() is impostor
    finally:
        loop.close()


def test_connected_is_false_before_the_close_is_awaited(handler, manager):
    # r1 M15: a consumer running during the close must already see "down".
    _connect(handler)
    seen = []

    async def _disconnect():
        seen.append(handler._connected)
    _Session.made[0].disconnect = _disconnect

    asyncio.run(handler._handle_disconnection("x"))

    assert seen == [False]


def test_a_close_that_never_returns_cannot_park_the_loop(handler, manager,
                                                         monkeypatch, caplog):
    # r1 LOW-2: an unbounded await held the registration and ignored stop.
    _connect(handler)

    async def _hang():
        await asyncio.Event().wait()
    _Session.made[0].disconnect = _hang
    monkeypatch.setattr(hm, "_TEARDOWN_CLOSE_TIMEOUT_S", 0.1)

    with caplog.at_level("WARNING"):
        asyncio.run(asyncio.wait_for(handler._handle_disconnection("x"), timeout=5))

    assert (handler._meshcore, manager.has_persistent()) == (None, False)
    assert any("close" in r.getMessage().lower() and r.levelname == "WARNING"
               for r in caplog.records)


@pytest.mark.parametrize("error", [RuntimeError("bug"), OSError("gone")],
                         ids=["generic-exception", "oserror"])
def test_both_loop_branches_announce_the_disconnect(error, handler, manager):
    # r1 INFO-2: the generic branch emitted no meshcore_disconnected status.
    statuses = []
    handler._notify_status = statuses.append
    calls = {"n": 0}

    async def _outbound():
        calls["n"] += 1
        if calls["n"] == 1:
            raise error
        if len(_Session.made) >= 2 and handler._connected:
            handler._stop_event.set()
    handler._process_outbound = _outbound

    async def _run():
        handler._loop = asyncio.get_running_loop()
        await asyncio.wait_for(handler._async_run(), timeout=5)
    with tx_guard.allow_meshcore_egress():   # mock radio, not RF
        asyncio.run(_run())

    assert statuses == ["meshcore_connected", "meshcore_disconnected", "meshcore_connected"]


def test_a_flapping_session_backs_off_instead_of_resetting_every_connect(handler, manager):
    # r1 LOW-1: record_success() on every connect zeroed the backoff, so a
    # persistent loop exception reconnected every ~4 s forever.
    from gateway.reconnect import ReconnectConfig, ReconnectStrategy
    handler._reconnect = ReconnectStrategy(
        config=ReconnectConfig(initial_delay=0.001, max_delay=0.01, max_attempts=50))

    async def _outbound():
        if len(_Session.made) >= 5:
            handler._stop_event.set()
            return
        raise RuntimeError("persistent bug")
    handler._process_outbound = _outbound

    async def _run():
        handler._loop = asyncio.get_running_loop()
        await asyncio.wait_for(handler._async_run(), timeout=5)
    with tx_guard.allow_meshcore_egress():   # mock radio, not RF
        asyncio.run(_run())

    assert handler._reconnect.attempts >= 4      # grew; never reset by a 0 s session


def test_a_session_that_stays_up_resets_the_backoff(handler, manager, monkeypatch):
    # the control for the test above: stable = success.
    from gateway.reconnect import ReconnectConfig, ReconnectStrategy
    monkeypatch.setattr(hm, "_SESSION_STABLE_S", 0.0)
    handler._reconnect = ReconnectStrategy(config=ReconnectConfig(initial_delay=0.001))
    handler._reconnect.attempts = 3
    calls = {"n": 0}

    async def _outbound():
        calls["n"] += 1
        if calls["n"] >= 2:
            handler._stop_event.set()
    handler._process_outbound = _outbound

    async def _run():
        handler._loop = asyncio.get_running_loop()
        await asyncio.wait_for(handler._async_run(), timeout=5)
    with tx_guard.allow_meshcore_egress():   # mock radio, not RF
        asyncio.run(_run())

    assert handler._reconnect.attempts == 0


class _CountingRadio:
    def __init__(self):
        self.adverts = 0

    async def send_advert(self, flood=False):
        self.adverts += 1


def _run_heartbeat_briefly(h, seconds=0.05):
    async def _go():
        task = asyncio.create_task(h._advert_heartbeat_loop(600, False))
        await asyncio.sleep(seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(_go())


def test_a_reconnect_never_adverts_faster_than_the_configured_interval(handler):
    # r1 LOW-1 (RF): one advert per reconnect cycle, whatever the interval.
    handler._radio = _CountingRadio()
    handler._connected = True
    for _ in range(3):                           # three connects in a row
        _run_heartbeat_briefly(handler)

    assert handler._radio.adverts == 1


def test_a_fresh_process_still_adverts_immediately(handler):
    # control: the first connect of a process is unchanged.
    handler._radio = _CountingRadio()
    handler._connected = True
    _run_heartbeat_briefly(handler)

    assert handler._radio.adverts == 1


def test_sync_disconnect_never_unregisters_a_foreign_link(handler, manager):
    # r1 INFO-1: the unchanged sync disconnect() contradicted the contract.
    foreign = object()
    loop = asyncio.new_event_loop()
    try:
        manager.register_persistent(foreign, loop, owner="someone-else")
        handler._meshcore = None

        handler.disconnect()

        assert manager.get_meshcore() is foreign
    finally:
        loop.close()


def test_sync_disconnect_says_so_when_our_unregister_fails(handler, manager,
                                                         monkeypatch, caplog):
    # mutant e09: the same "a stuck registration is never silent" rule as teardown.
    _connect(handler)
    handler._loop = None                         # the loop is gone: skip the async close
    monkeypatch.setattr(manager, "unregister_persistent",
                        lambda: (_ for _ in ()).throw(RuntimeError("stuck")))

    with caplog.at_level("WARNING"):
        handler.disconnect()

    assert any(r.levelname == "WARNING" and "unregister" in r.getMessage()
               for r in caplog.records)


# ===========================================================================
# Teardown review r2 (Fable, 2026-11-19): 0 H / 0 M / 2 L / 4 I.
# ===========================================================================

class _FailingRadio(_CountingRadio):
    def __init__(self):
        super().__init__()
        self.fail = True

    async def send_advert(self, flood=False):
        if self.fail:
            raise OSError("link dying")
        self.adverts += 1


def _heartbeat_for(h, interval, seconds):
    async def _go():
        task = asyncio.create_task(h._advert_heartbeat_loop(interval, False))
        await asyncio.sleep(seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(_go())


def test_a_failed_advert_delays_the_next_sessions_advert_by_at_most_half(handler):
    # r2 LOW-1 + r3 LOW-A: after a FAILED attempt a reconnect waits half an
    # interval (an ack-lost send may already have keyed the radio), then
    # adverts — never the 2x silence of a pre-send stamp.
    handler._radio = _FailingRadio()
    handler._connected = True
    observed = {}

    async def _go():
        first = asyncio.create_task(handler._advert_heartbeat_loop(1.0, False))
        await asyncio.sleep(0.05)                    # the attempt at ~t0 fails
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass
        handler._radio.fail = False
        task = asyncio.create_task(handler._advert_heartbeat_loop(1.0, False))
        await asyncio.sleep(0.25)                    # ~t0+0.3: inside the half-interval floor
        observed["floor"] = handler._radio.adverts
        await asyncio.sleep(0.4)                     # ~t0+0.7: past it
        observed["after"] = handler._radio.adverts
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(_go())

    assert observed == {"floor": 0, "after": 1}


class _AckLostRadio(_CountingRadio):
    """send_advert reaches the companion (counted as a TX) but no OK returns."""

    async def send_advert(self, flood=False):
        self.adverts += 1
        raise TimeoutError("no OK from companion")


def test_an_ack_lost_advert_is_not_re_sent_on_every_reconnect(handler):
    # r3 LOW-A: stamp-on-success alone re-sent on EVERY reconnect when the
    # command was delivered but its OK lost (69.5 TX/h vs a 6/h budget).
    handler._radio = _AckLostRadio()
    handler._connected = True
    for _ in range(4):                               # four quick reconnects
        _heartbeat_for(handler, 600, 0.05)

    assert handler._radio.adverts == 1


def test_an_advert_cancelled_mid_send_leaves_a_witness(handler, caplog):
    # r3: CancelledError bypassed `except Exception` - 147 TX, zero log lines.
    class _SlowRadio(_CountingRadio):
        async def send_advert(self, flood=False):
            self.adverts += 1
            await asyncio.sleep(10)
    handler._radio = _SlowRadio()
    handler._connected = True
    with caplog.at_level("INFO"):
        _heartbeat_for(handler, 600, 0.05)

    assert any("cancel" in r.getMessage().lower() for r in caplog.records)


def test_the_heartbeat_keeps_its_cadence_within_a_session(handler):
    # r3 C8: a `return` after the first fire passed every test.
    handler._radio = _CountingRadio()
    handler._connected = True
    _heartbeat_for(handler, 0.3, 0.75)               # fires at ~0, 0.3, 0.6

    assert handler._radio.adverts >= 3


def test_stop_during_a_deferred_wait_sends_nothing(handler):
    # r3 C5: no advert during shutdown.
    handler._radio = _CountingRadio()
    handler._connected = True
    _heartbeat_for(handler, 1.0, 0.05)

    async def _go():
        task = asyncio.create_task(handler._advert_heartbeat_loop(1.0, False))
        await asyncio.sleep(0.2)
        handler._stop_event.set()
        await asyncio.sleep(1.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(_go())

    assert handler._radio.adverts == 1


def test_a_fired_advert_is_logged_at_info(handler, caplog):
    # r3 C10: the operator's witness that the node is announcing.
    handler._radio = _CountingRadio()
    handler._connected = True
    with caplog.at_level("INFO"):
        _heartbeat_for(handler, 600, 0.05)

    assert any(r.levelname == "INFO" and "fired" in r.getMessage() for r in caplog.records)


def test_the_stable_session_threshold_is_pinned():
    # r3 C19: tests patch it to 0, so its value went unseen.
    assert hm._SESSION_STABLE_S == 60.0


def test_a_deferred_advert_fires_when_its_interval_comes_due(handler):
    # r2 LOW-2 (B3 return-after-wait; B5 waits a FULL interval instead of the
    # remainder): a reconnect 0.5 s after an advert, interval 1.0 s, must
    # advert at ~t0+1.0 — not never (B3), not at ~t0+1.5 (B5).
    handler._radio = _CountingRadio()
    handler._connected = True
    observed = {}

    async def _go():
        first = asyncio.create_task(handler._advert_heartbeat_loop(1.0, False))
        await asyncio.sleep(0.05)                    # advert at ~t0
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.45)                    # the link was down ~0.5 s
        task = asyncio.create_task(handler._advert_heartbeat_loop(1.0, False))
        await asyncio.sleep(0.25)                    # ~t0+0.75: still inside
        observed["early"] = handler._radio.adverts
        await asyncio.sleep(0.45)                    # ~t0+1.2: due (B5 would be ~1.5)
        observed["due"] = handler._radio.adverts
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(_go())

    assert observed == {"early": 1, "due": 2}


def test_a_deferred_advert_is_cancelled_if_the_link_drops_during_the_wait(handler):
    # r2 B4: no stop/connected re-check after the wait.
    handler._radio = _CountingRadio()
    handler._connected = True
    _heartbeat_for(handler, 1.0, 0.05)

    async def _go():
        task = asyncio.create_task(handler._advert_heartbeat_loop(1.0, False))
        await asyncio.sleep(0.3)
        handler._connected = False                   # the link dropped mid-wait
        await asyncio.sleep(1.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(_go())

    assert handler._radio.adverts == 1


class _RecordingHealth:
    def __init__(self):
        self.events = []

    def record_connection_event(self, kind, event, *a, **k):
        self.events.append(event)

    def record_error(self, *a, **k):
        return "test"


@pytest.mark.parametrize("error, expected", [
    (RuntimeError("bug"), ["retry", "connected", "error", "disconnected", "retry", "connected"]),
    (OSError("gone"), ["retry", "connected", "disconnected", "retry", "connected"]),
], ids=["generic-exception", "oserror"])
def test_each_loop_branch_records_its_health_events(error, expected, handler, manager):
    # r2 A12 A19: the health object was a no-op lambda, so dropping either
    # event went unseen.
    handler.health = _RecordingHealth()
    calls = {"n": 0}

    async def _outbound():
        calls["n"] += 1
        if calls["n"] == 1:
            raise error
        if len(_Session.made) >= 2 and handler._connected:
            handler._stop_event.set()
    handler._process_outbound = _outbound

    async def _run():
        handler._loop = asyncio.get_running_loop()
        await asyncio.wait_for(handler._async_run(), timeout=5)
    with tx_guard.allow_meshcore_egress():   # mock radio, not RF
        asyncio.run(_run())

    assert handler.health.events == expected


def test_the_oserror_branch_backs_off_too(handler, manager):
    # r2 A11: the flap test only raised RuntimeError.
    from gateway.reconnect import ReconnectConfig, ReconnectStrategy
    handler._reconnect = ReconnectStrategy(
        config=ReconnectConfig(initial_delay=0.001, max_delay=0.01, max_attempts=50))

    async def _outbound():
        if len(_Session.made) >= 5:
            handler._stop_event.set()
            return
        raise OSError("serial gone")
    handler._process_outbound = _outbound

    async def _run():
        handler._loop = asyncio.get_running_loop()
        await asyncio.wait_for(handler._async_run(), timeout=5)
    with tx_guard.allow_meshcore_egress():   # mock radio, not RF
        asyncio.run(_run())

    assert handler._reconnect.attempts >= 4


def test_sync_disconnect_keeps_an_impostor_under_our_owner_name(handler, manager):
    # r2 A14: identity, never the owner name, on the sync path too.
    impostor = object()
    loop = asyncio.new_event_loop()
    try:
        manager.register_persistent(impostor, loop, owner="gateway-bridge")
        handler._meshcore = _Session()
        handler._loop = None

        handler.disconnect()

        assert manager.get_meshcore() is impostor
    finally:
        loop.close()


def test_the_teardown_close_bound_matches_the_sync_disconnect_bound():
    # r2 A22: the bound is a number an operator waits on; pin it.
    assert hm._TEARDOWN_CLOSE_TIMEOUT_S == 5.0


# ===========================================================================
# Teardown review r4 (Fable, 2026-11-19): 0 H / 0 M / 1 L / 4 I — no code
# defect; these close its test gaps.
# ===========================================================================

def test_an_advert_cancelled_mid_ack_is_not_re_sent_on_every_reconnect(handler):
    # r4 LOW-1 (mutant N8): the attempt stamp must be set BEFORE the send so a
    # send cut off mid-ack (already delivered) still floors the next attempt.
    class _SlowRadio(_CountingRadio):
        async def send_advert(self, flood=False):
            self.adverts += 1
            await asyncio.sleep(10)
    handler._radio = _SlowRadio()
    handler._connected = True
    for _ in range(4):                               # each session dies mid-ack
        _heartbeat_for(handler, 600, 0.05)

    assert handler._radio.adverts == 1


def test_after_a_real_success_the_full_interval_floor_wins(handler):
    # r4 INFO-1 (N7 min-of-floors, N20 last-floor-wins): after a SUCCESS both
    # stamps are set; the longer (success, `interval`) floor must win.
    handler._radio = _CountingRadio()
    handler._connected = True
    _heartbeat_for(handler, 1.0, 0.05)               # success at ~t0

    async def _go():
        # reconnect at ~t0+0.2: BOTH floors active — success says ~t0+1.0,
        # attempt says ~t0+0.5; max must win (min / last-wins would fire at 0.5)
        await asyncio.sleep(0.15)
        task = asyncio.create_task(handler._advert_heartbeat_loop(1.0, False))
        await asyncio.sleep(0.5)                     # ~t0+0.7
        early = handler._radio.adverts
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return early
    assert asyncio.run(_go()) == 1                   # no advert before ~t0+1.0


def test_a_failed_advert_is_logged_at_warning(handler, caplog):
    # r4 INFO-3 (N25).
    handler._radio = _FailingRadio()
    handler._connected = True
    with caplog.at_level("WARNING"):
        _heartbeat_for(handler, 600, 0.05)

    assert any(r.levelname == "WARNING" and "heartbeat failed" in r.getMessage()
               for r in caplog.records)


def test_the_backoff_resets_once_the_session_reaches_the_threshold(handler, manager,
                                                                  monkeypatch):
    # r4 INFO-4 (N27 `>= 2 * threshold`): reset exactly at the threshold, not later.
    from gateway.reconnect import ReconnectConfig, ReconnectStrategy
    monkeypatch.setattr(hm, "_SESSION_STABLE_S", 0.3)
    handler._reconnect = ReconnectStrategy(config=ReconnectConfig(initial_delay=0.001))
    handler._reconnect.attempts = 3
    started = {}

    async def _outbound():
        started.setdefault("t", hm.time.monotonic())
        if hm.time.monotonic() - started["t"] >= 0.45:   # past 1x, short of 2x
            handler._stop_event.set()
    handler._process_outbound = _outbound

    async def _run():
        handler._loop = asyncio.get_running_loop()
        await asyncio.wait_for(handler._async_run(), timeout=5)
    with tx_guard.allow_meshcore_egress():   # mock radio, not RF
        asyncio.run(_run())

    assert handler._reconnect.attempts == 0
