"""CS-1 MeshCore observer: companion RX_LOG_DATA -> meshcoretomqtt-shaped packet.

Seam 1 (pure translator). The consumer is CoreScope's ingestor, "Format 1: Raw
packet (meshcoretomqtt / Cisien format)" (CoreScope v3.18.0
``cmd/ingestor/main.go:1089``): topic ``meshcore/<IATA>/<observer>/packets``,
JSON with a hex ``raw`` that CoreScope decodes itself.

The event payload is meshcore_py 2.3.7's ``RX_LOG_DATA`` dict
(``reader.py`` LOG_DATA branch): ``raw_hex`` is everything after the 0x88
byte INCLUDING the SNR and RSSI bytes; ``payload`` is the on-air packet only.
Research: MeshForge ``.claude/research/corescope_cs1_2026_11_18.md``.
"""

import pytest

from gateway.meshcore_observer import Refused, build_packet_message

KEY = "AB" * 32  # observer = the companion's public key (64 hex chars)

# An on-air packet as the radio heard it: header 0x11 (FLOOD, ADVERT),
# path_len 0, then payload bytes.
PACKET = bytes.fromhex("11000102030405")


def _event_payload(packet=PACKET, snr=-6.25, rssi=-101):
    """The dict meshcore_py 2.3.7 dispatches for a 0x88 frame."""
    snr_byte = int(snr * 4) & 0xFF
    rssi_byte = rssi & 0xFF
    return {
        "raw_hex": bytes([snr_byte, rssi_byte]).hex() + packet.hex(),
        "recv_time": 1795060800,
        "snr": snr,
        "rssi": rssi,
        "payload": packet.hex(),
        "payload_length": len(packet),
    }


def test_raw_is_the_on_air_packet_not_raw_hex():
    msg = build_packet_message(_event_payload(), iata="ITO", observer_key=KEY)

    assert msg.topic == f"meshcore/ITO/{KEY}/packets"
    assert bytes.fromhex(msg.body["raw"]) == PACKET


def test_timestamp_is_the_receipt_time_with_an_explicit_utc_offset():
    # recv_time 1795060800 = 2026-11-19T04:00:00Z (`date -u -d @1795060800`).
    # CoreScope clamps a zone-less timestamp; it must carry +00:00 (main.go:1726-1760).
    msg = build_packet_message(_event_payload(), iata="ITO", observer_key=KEY)

    assert msg.body["timestamp"] == "2026-11-19T04:00:00+00:00"


def test_snr_and_rssi_are_numbers_from_the_frame():
    msg = build_packet_message(_event_payload(snr=-6.25, rssi=-101),
                               iata="ITO", observer_key=KEY)

    assert msg.body["SNR"] == -6.25
    assert msg.body["RSSI"] == -101


def _without(key):
    p = _event_payload()
    del p[key]
    return p


@pytest.mark.parametrize("payload, iata, key, reason", [
    # A 0x88 frame of <= 3 bytes carries no packet: meshcore_py omits "payload".
    (_without("payload"), "ITO", KEY, "no_packet"),
    ({**_event_payload(), "payload": ""}, "ITO", KEY, "no_packet"),
    ({**_event_payload(), "payload": "zz11"}, "ITO", KEY, "not_hex"),
    (_without("recv_time"), "ITO", KEY, "no_recv_time"),
    (_event_payload(), "ito", KEY, "bad_iata"),
    (_event_payload(), "ITOX", KEY, "bad_iata"),
    (_event_payload(), "ITO", "AB" * 31, "bad_observer_key"),
    (_event_payload(), "ITO", "UNKNOWN", "bad_observer_key"),
], ids=["no-payload-key", "empty-payload", "non-hex", "no-recv-time",
        "lowercase-iata", "four-letter-iata", "short-key", "unknown-key"])
def test_input_the_author_cannot_have_meant_is_refused_with_a_reason(
        payload, iata, key, reason):
    result = build_packet_message(payload, iata=iata, observer_key=key)

    assert result == Refused(reason)


# ---------------------------------------------------------------------------
# Seam 2: the observer witness (what a probe / the TUI reads)
# ---------------------------------------------------------------------------

from gateway.meshcore_observer import ObserverWitness  # noqa: E402


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_a_fresh_witness_says_never_and_states_the_blind_spot():
    snap = ObserverWitness(clock=_Clock()).snapshot()

    assert snap["state"] == "never"          # not "ok with 0 packets"
    assert snap["queued"] == 0
    # 0x88 is sent only if len + 3 <= MAX_FRAME_SIZE (176): MeshCore
    # examples/companion_radio/MyMesh.cpp:292 @ 75b04ef0.
    assert snap["unobservable_over_bytes"] == 173


def _queued(witness, length):
    msg = build_packet_message(_event_payload(packet=bytes(length)),
                               iata="ITO", observer_key=KEY)
    witness.record_queued(msg)


def test_queued_packets_land_in_length_buckets_up_to_the_ceiling():
    w = ObserverWitness(clock=_Clock())
    for length in (7, 32, 33, 160, 161, 173):
        _queued(w, length)

    snap = w.snapshot()
    assert snap["state"] == "ok"
    assert snap["queued"] == 6
    assert snap["length_buckets"] == {
        "1-32": 2, "33-64": 1, "65-96": 0, "97-128": 0,
        "129-160": 1, "161-173": 2,
    }


def test_refusals_and_publish_failures_are_counted_by_reason():
    w = ObserverWitness(clock=_Clock())
    w.record_refused(Refused("no_packet"))
    w.record_refused(Refused("no_packet"))
    w.record_refused(Refused("not_hex"))
    msg = build_packet_message(_event_payload(), iata="ITO", observer_key=KEY)
    w.record_publish_failed(msg, ConnectionRefusedError("broker down"))

    snap = w.snapshot()
    assert snap["refused"] == {"no_packet": 2, "not_hex": 1}
    assert snap["publish_failed"] == 1
    assert "broker down" in snap["last_error"]
    assert snap["queued"] == 0
    assert snap["state"] == "failing"   # tried and failed is not "never"


def test_last_queued_age_comes_from_the_monotonic_clock_and_is_none_before_any():
    clock = _Clock(1000.0)
    w = ObserverWitness(clock=clock)
    assert w.snapshot()["last_queued_age_s"] is None

    _queued(w, 7)
    clock.t = 1042.5
    assert w.snapshot()["last_queued_age_s"] == 42.5


def test_a_packet_above_the_ceiling_contradicts_the_stated_blind_spot():
    # If firmware ever raises MAX_FRAME_SIZE, our "unobservable over 173 B"
    # claim is wrong: say so, never absorb it into a bucket (hfm #3).
    w = ObserverWitness(clock=_Clock())
    _queued(w, 174)

    snap = w.snapshot()
    assert snap["ceiling_contradicted"] == 1
    assert sum(snap["length_buckets"].values()) == 0


# ---------------------------------------------------------------------------
# Seam 3: handler wiring (fake meshcore + fake publisher; no radio, no broker)
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
import threading  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from gateway.meshcore_handler import MeshCoreHandler  # noqa: E402


class _EventType:
    RX_LOG_DATA = "rx_log_data"


class _FakeMeshCore:
    def __init__(self, self_info=None):
        self.subscribed = []
        self.self_info = self_info if self_info is not None else {
            "public_key": KEY.lower(), "name": "MA-observer"}

    def subscribe(self, event_type, cb):
        self.subscribed.append((event_type, cb))
        return object()


class _FakePublisher:
    def __init__(self, error=None):
        self.sent = []
        self.error = error

    def publish(self, topic, body):
        if self.error is not None:
            raise self.error
        self.sent.append((topic, body))


def _bare_handler(enabled, iata="ITO", mc=None):
    h = MeshCoreHandler.__new__(MeshCoreHandler)
    h.stats = {}
    h._stats_lock = threading.Lock()
    h._subscriptions = []
    h._meshcore = mc if mc is not None else _FakeMeshCore()
    h._connected = True                   # radio up (state table v2 row 3)
    h.config = SimpleNamespace(meshcore=SimpleNamespace(
        observer_enabled=enabled, observer_iata=iata))
    return h


def test_observer_off_subscribes_nothing_and_says_disabled():
    mc = _FakeMeshCore()
    h = _bare_handler(enabled=False, mc=mc)
    pub = _FakePublisher()

    h._init_observer(_EventType, publisher=pub)

    assert mc.subscribed == []
    assert h._subscriptions == []
    assert h.observer_snapshot()["state"] == "disabled"


def _enabled_handler(pub, mc=None):
    mc = mc if mc is not None else _FakeMeshCore()
    h = _bare_handler(enabled=True, mc=mc)
    h._init_observer(_EventType, publisher=pub)
    return h, mc


def _fire(mc, payload):
    (_, cb), = [s for s in mc.subscribed if s[0] == _EventType.RX_LOG_DATA]
    asyncio.run(cb(SimpleNamespace(payload=payload)))


def test_observer_on_publishes_each_rx_log_event_under_the_radios_own_key():
    pub = _FakePublisher()
    h, mc = _enabled_handler(pub)

    _fire(mc, _event_payload())

    # self_info carries the key lower-case; the topic uses it upper-case,
    # as meshcoretomqtt's {PUBLIC_KEY} does (BELIEVED from its topic docs).
    (topic, body), = pub.sent
    assert topic == f"meshcore/ITO/{KEY}/packets"
    assert bytes.fromhex(body["raw"]) == PACKET
    assert body["origin"] == "MA-observer"
    assert h.observer_snapshot()["queued"] == 1


def test_a_broker_error_is_counted_and_never_raised_into_the_radio_loop():
    pub = _FakePublisher(error=ConnectionRefusedError("broker down"))
    h, mc = _enabled_handler(pub)

    _fire(mc, _event_payload())          # must not raise

    snap = h.observer_snapshot()
    assert snap["publish_failed"] == 1
    assert snap["state"] == "failing"
    assert "broker down" in snap["last_error"]


@pytest.mark.parametrize("payload", [
    {k: v for k, v in _event_payload().items() if k not in ("snr", "rssi")},
    "not-a-dict",
    None,
], ids=["no-snr-rssi", "string-payload", "none-payload"])
def test_odd_events_never_raise_into_the_radio_loop(payload):
    pub = _FakePublisher()
    h, mc = _enabled_handler(pub)

    _fire(mc, payload)                   # must not raise

    snap = h.observer_snapshot()
    # every event lands in exactly one counter — nothing uncounted
    assert (snap["queued"] + sum(snap["refused"].values())
            + snap["publish_failed"] + snap["internal_errors"]) == 1


def test_a_failed_subscribe_is_witnessed_not_raised():
    mc = _FakeMeshCore()
    mc.subscribe = None                  # not callable -> TypeError inside
    h = _bare_handler(enabled=True, mc=mc)

    h._init_observer(_EventType, publisher=_FakePublisher())   # must not raise

    assert h._subscriptions == []
    assert h.observer_snapshot()["state"] == "subscribe_failed"


def test_a_none_payload_is_refused_as_not_a_dict_not_masked_as_empty():
    pub = _FakePublisher()
    h, mc = _enabled_handler(pub)

    _fire(mc, None)

    assert h.observer_snapshot()["refused"] == {"not_a_dict": 1}


def test_an_internal_bug_is_counted_never_raised(monkeypatch):
    import gateway.meshcore_observer_mixin as mixin

    def _boom(*a, **k):
        raise RuntimeError("translator bug")
    monkeypatch.setattr(mixin, "build_packet_message", _boom)
    pub = _FakePublisher()
    h, mc = _enabled_handler(pub)

    _fire(mc, _event_payload())          # must not raise

    snap = h.observer_snapshot()
    assert snap["internal_errors"] == 1  # our bug: its own counter, not a refusal
    assert snap["refused"] == {}


def test_a_self_info_that_is_not_a_dict_is_refused_not_raised():
    mc = _FakeMeshCore(self_info="garbage")
    pub = _FakePublisher()
    h, _ = _enabled_handler(pub, mc=mc)

    _fire(mc, _event_payload())          # must not raise

    assert h.observer_snapshot()["refused"] == {"bad_observer_key": 1}


def test_the_real_config_class_has_the_observer_off_and_no_iata_by_default():
    from gateway.config import MeshCoreConfig
    cfg = MeshCoreConfig()

    assert cfg.observer_enabled is False
    assert cfg.observer_iata == ""       # declared per box, never guessed
    assert MeshCoreConfig(observer_enabled=True, observer_iata="ITO").observer_iata == "ITO"


def test_observer_on_without_an_mqtt_client_is_loud_and_subscribes_nothing(monkeypatch):
    import gateway.meshcore_observer_mixin as mixin
    monkeypatch.setattr(mixin, "make_local_publisher", lambda: None)
    mc = _FakeMeshCore()
    h = _bare_handler(enabled=True, mc=mc)

    h._init_observer(_EventType)         # no publisher passed -> the default factory

    assert mc.subscribed == []
    assert h.observer_snapshot()["state"] == "no_mqtt_client"


class _FakePahoInfo:
    def __init__(self, rc):
        self.rc = rc


class _FakePaho:
    def __init__(self, rc=0):
        self.calls = []
        self.rc = rc

    def publish(self, topic, payload, qos, retain):
        self.calls.append((topic, payload, qos, retain))
        return _FakePahoInfo(self.rc)


def test_local_publisher_sends_json_at_qos0_unretained():
    import json
    from gateway.meshcore_observer import LocalBrokerPublisher
    paho = _FakePaho()

    LocalBrokerPublisher(client=paho).publish("meshcore/ITO/K/packets", {"raw": "11"})

    (topic, payload, qos, retain), = paho.calls
    assert topic == "meshcore/ITO/K/packets"
    assert json.loads(payload) == {"raw": "11"}
    assert (qos, retain) == (0, False)   # packets are never retained (meshcoretomqtt)


def test_local_publisher_raises_on_a_non_success_rc_so_the_witness_counts_it():
    from gateway.meshcore_observer import LocalBrokerPublisher
    with pytest.raises(ConnectionError):
        LocalBrokerPublisher(client=_FakePaho(rc=4)).publish("t", {"raw": "11"})


def test_a_reconnect_reuses_one_mqtt_client_never_a_new_one_per_connect(monkeypatch):
    # _subscribe_events runs on every (re)connect; a client per connect leaks
    # a paho network thread each time.
    import gateway.meshcore_observer_mixin as mixin
    made = []
    monkeypatch.setattr(mixin, "make_local_publisher",
                        lambda: made.append(1) or _FakePublisher())
    h = _bare_handler(enabled=True)

    h._init_observer(_EventType)
    h._meshcore = _FakeMeshCore()         # the reconnect hands a new radio session
    h._init_observer(_EventType)

    assert len(made) == 1


def test_a_later_successful_subscribe_clears_an_earlier_failure():
    mc = _FakeMeshCore()
    mc.subscribe = None
    h = _bare_handler(enabled=True, mc=mc)
    pub = _FakePublisher()
    h._init_observer(_EventType, publisher=pub)
    assert h.observer_snapshot()["state"] == "subscribe_failed"

    h._meshcore = _FakeMeshCore()         # reconnect, subscribe works now
    h._init_observer(_EventType, publisher=pub)

    assert h.observer_snapshot()["state"] == "never"


def test_subscribe_events_wires_the_observer_in(monkeypatch):
    import gateway.meshcore_handler as handler_mod

    class _AllEvents:
        CONTACT_MSG_RECV = "c"
        CHANNEL_MSG_RECV = "ch"
        ADVERTISEMENT = "ad"
        ACK = "ack"
        NEW_CONTACT = "nc"
        RX_LOG_DATA = "rx_log_data"
    monkeypatch.setattr(handler_mod, "_meshcore_mod", SimpleNamespace(EventType=_AllEvents))
    mc = _FakeMeshCore()
    h = _bare_handler(enabled=True, mc=mc)
    h.config.meshcore.repliable_contacts = []
    h._observer_publisher = _FakePublisher()

    h._subscribe_events()

    assert "rx_log_data" in [e for e, _ in mc.subscribed]


# ---------------------------------------------------------------------------
# Translator strictness (review r1 LOW-6, LOW-7, m10, m11). Table:
# ~/.claude/plans/cs1_r1_review_2026_11_18/TRANSLATOR_TABLE.md. Rule: an
# optional field ABSENT is omitted; PRESENT but garbage refuses the packet with
# a named reason — never dropped silently, never passed on to CoreScope.
# ---------------------------------------------------------------------------

import json  # noqa: E402
import math  # noqa: E402


def _with(**kw):
    return {**_event_payload(), **kw}


def _refused_as(payload, origin=None):
    r = build_packet_message(payload, iata="ITO", observer_key=KEY, origin=origin)
    return r.reason if isinstance(r, Refused) else None


@pytest.mark.parametrize("packet", [
    "11 00 01",          # bytes.fromhex skips spaces: passed in r1
    "1100 ",
    "\t1100",
    "110",               # odd length
    "zz",
    b"1100",             # bytes, not a hex str
    1100,
    "11" * 256,          # > MAX_TRANS_UNIT 255: not a LoRa packet
    0,                   # falsy non-str: garbage, not "no packet"
    b"",
], ids=["inner-space", "trailing-space", "leading-tab", "odd-length", "non-hex",
        "bytes", "int", "256-bytes", "zero", "empty-bytes"])
def test_a_packet_that_is_not_clean_hex_is_refused(packet):
    assert _refused_as(_with(payload=packet)) == "not_hex"


@pytest.mark.parametrize("recv_time", [
    True, False,                          # bool is not a time (passed in r1)
    -1, 0,                                # passed in r1 (1969 / epoch)
    math.nan, math.inf, -math.inf,        # NaN -> internal_error in r1
    "1795060800",                         # str -> internal_error in r1
    5_000_000_000,                        # year 2128 -> internal_error in r1
])
def test_a_recv_time_that_is_not_a_sane_time_is_refused_by_name(recv_time):
    assert _refused_as(_with(recv_time=recv_time)) == "bad_recv_time"


def test_a_float_recv_time_is_accepted():
    # UNKNOWN whether meshcore_py 2.3.7 hands int or float: both work.
    assert _refused_as(_with(recv_time=1795060800.5)) is None


@pytest.mark.parametrize("snr", [True, False, math.nan, math.inf, -math.inf, "-6.25"])
def test_a_present_snr_that_is_not_a_finite_number_is_refused(snr):
    assert _refused_as(_with(snr=snr)) == "bad_snr"


@pytest.mark.parametrize("rssi", [True, False, math.nan, math.inf, -101.5, "-101"])
def test_a_present_rssi_that_is_not_an_int_is_refused(rssi):
    assert _refused_as(_with(rssi=rssi)) == "bad_rssi"


def test_absent_snr_and_rssi_are_omitted_not_refused():
    p = _event_payload()
    del p["snr"], p["rssi"]
    msg = build_packet_message(p, iata="ITO", observer_key=KEY)

    assert not isinstance(msg, Refused)
    assert "SNR" not in msg.body and "RSSI" not in msg.body


def test_an_int_snr_is_a_number_too():
    msg = build_packet_message(_with(snr=5), iata="ITO", observer_key=KEY)
    assert msg.body["SNR"] == 5


@pytest.mark.parametrize("origin", [b"MA-observer", 5, ["MA"]])
def test_an_origin_that_is_not_a_str_is_refused_not_blamed_on_the_broker(origin):
    # LOW-7: a bytes name failed json.dumps at publish -> "failing", broker blamed.
    assert _refused_as(_event_payload(), origin=origin) == "bad_origin"


@pytest.mark.parametrize("origin", [None, ""])
def test_an_absent_origin_is_omitted(origin):
    msg = build_packet_message(_event_payload(), iata="ITO", observer_key=KEY, origin=origin)
    assert "origin" not in msg.body


@pytest.mark.parametrize("bad", [None, 5, b"ITO"])
def test_a_non_str_iata_or_key_is_refused_never_raised(bad):
    assert build_packet_message(_event_payload(), iata=bad,
                                observer_key=KEY).reason == "bad_iata"
    assert build_packet_message(_event_payload(), iata="ITO",
                                observer_key=bad).reason == "bad_observer_key"


def test_the_first_refusal_in_table_order_wins():
    assert _refused_as(_with(snr=math.nan, recv_time=True, payload="zz"),
                       origin=b"x") == "not_hex"
    assert build_packet_message(_with(snr=math.nan), iata="ito",
                                observer_key=KEY).reason == "bad_iata"
    assert _refused_as(_with(snr=True, rssi=True), origin=b"x") == "bad_snr"
    assert _refused_as(_with(rssi=True), origin=b"x") == "bad_rssi"


_VARIANTS = {
    "payload": ["1100", "11 00", "110", "", None, b"11", "AbCd"],
    "recv_time": [1795060800, 1795060800.5, True, -1, math.nan, "1", None, 10**400],
    "snr": [-6.25, 5, True, math.nan, math.inf, "x", None, 10**400],
    "rssi": [-101, True, math.nan, -101.5, "x", None, 10**400],
}


def _short(v):
    """Readable test ids: 10**400 would otherwise be a 400-char id."""
    t = str(v)
    return t if len(t) <= 12 else t[:8] + "..."


# --- r2 L3 + survivors: the wire's own ranges, the clock's floor, boundaries ---

@pytest.mark.parametrize("snr, ok", [
    (-32, True), (31.75, True), (0, True),     # signed byte / 4
    (32, False), (-32.25, False), (10**400, False), (-10**400, False), (1e300, False),
], ids=_short)
def test_snr_must_fit_the_wire_signed_byte_over_four(snr, ok):
    assert _refused_as(_with(snr=snr)) == (None if ok else "bad_snr")


@pytest.mark.parametrize("rssi, ok", [
    (-128, True), (127, True), (-101, True),   # signed byte
    (128, False), (-129, False), (5000, False), (10**400, False),
], ids=_short)
def test_rssi_must_fit_the_wire_signed_byte(rssi, ok):
    assert _refused_as(_with(rssi=rssi)) == (None if ok else "bad_rssi")


@pytest.mark.parametrize("recv_time, ok", [
    (1577836800, True),            # 2020-01-01: the floor itself
    (1577836799, False),           # RTC-less Pi before NTP (hfm #6)
    (1e-300, False),               # r2: published as 1970-01-01
    (4102444799, True),            # last second before the ceiling
    (4102444800, False),           # the ceiling itself (r2 t10)
    (10**400, False),              # r2 L3: OverflowError -> internal_error
], ids=_short)
def test_recv_time_lives_between_the_floor_and_the_ceiling(recv_time, ok):
    assert _refused_as(_with(recv_time=recv_time)) == (None if ok else "bad_recv_time")


def test_a_full_255_byte_packet_is_accepted():
    # r2 t08: MTU 254 went unseen.
    assert _refused_as(_with(payload="11" * 255)) is None


@pytest.mark.parametrize("iata, key, reason", [
    ("ITO/x", KEY, "bad_iata"),
    ("ITO", KEY + "/x", "bad_observer_key"),
    ("ITO", KEY + "\n", "bad_observer_key"),
], ids=["iata-junk", "key-junk", "key-newline"])
def test_trailing_junk_never_reaches_the_topic(iata, key, reason):
    # r2 t04: match-not-fullmatch put `<key>/x` into the topic, unpinned.
    assert build_packet_message(_event_payload(), iata=iata,
                                observer_key=key).reason == reason


def test_recv_time_is_checked_before_snr():
    # r2 t25.
    assert _refused_as(_with(recv_time=True, snr=True)) == "bad_recv_time"


def test_whatever_leaves_the_translator_is_strict_json():
    # The invariant: a refusal is the ONLY way garbage leaves. Every accepted
    # body survives json.dumps(allow_nan=False) — so no internal_error and no
    # publish_failed can come from the shape of the packet.
    import itertools
    origins = ["MA", None, "", b"MA"]
    for vals in itertools.product(*_VARIANTS.values(), origins):
        *fields, origin = vals
        p = {"payload_length": 2}
        for k, v in zip(_VARIANTS, fields):
            if v is not None:
                p[k] = v
        r = build_packet_message(p, iata="ITO", observer_key=KEY, origin=origin)
        if not isinstance(r, Refused):
            json.dumps(r.body, allow_nan=False)        # must not raise
            assert all(c in "0123456789abcdefABCDEF" for c in r.body["raw"])


# --- config: a non-bool observer_enabled never switches the observer ON ----

@pytest.mark.parametrize("val", ["false", "true", 1, 0, None])
def test_a_non_bool_observer_enabled_is_off_and_logged_loudly(val, caplog):
    from gateway.config import MeshCoreConfig
    with caplog.at_level("ERROR"):
        cfg = MeshCoreConfig(observer_enabled=val)

    assert cfg.observer_enabled is False
    assert any("observer_enabled" in r.getMessage() and r.levelname == "ERROR"
               for r in caplog.records)
