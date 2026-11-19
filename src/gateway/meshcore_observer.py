"""CS-1 MeshCore observer: companion RX_LOG_DATA -> meshcoretomqtt-shaped packet.

The companion pushes every received on-air packet as frame 0x88
(PUSH_CODE_LOG_RX_DATA). meshcore_py raises it as ``EventType.RX_LOG_DATA``
with ``payload`` = the on-air packet hex (``raw_hex`` also carries the SNR and
RSSI bytes, so it is NOT the packet). CoreScope ingests the packet as
"Format 1" on ``meshcore/<IATA>/<observer key>/packets`` and decodes ``raw``
itself. Research: MeshForge ``.claude/research/corescope_cs1_2026_11_18.md``.

Input the author cannot have meant is REFUSED with a reason (never passed on
to a shared instrument): the caller counts each reason (hfm #3, #9).
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

# External dep, optional: absent -> make_local_publisher() is None -> the
# observer reports no_mqtt_client. Module-level so a test patches ONE name.
try:
    import paho.mqtt.client as _mqtt
except ImportError:  # pragma: no cover - depends on the box
    _mqtt = None

_IATA = re.compile(r"[A-Z]{3}")
_OBSERVER_KEY = re.compile(r"[0-9A-Fa-f]{64}")


@dataclass(frozen=True)
class PacketMessage:
    topic: str
    body: Dict[str, Any]


@dataclass(frozen=True)
class Refused:
    reason: str


# A LoRa packet: 1..MAX_TRANS_UNIT bytes as clean hex — no whitespace
# (``bytes.fromhex`` skips it), even length. MAX_TRANS_UNIT 255 (MeshCore).
MAX_TRANS_UNIT = 255
_PACKET_HEX = re.compile(r"(?:[0-9A-Fa-f]{2}){1,%d}" % MAX_TRANS_UNIT)
# Receipt time window. Floor 2020-01-01: an RTC-less Pi before NTP stamps
# 1970 (hfm #6) — refused loudly, never stored as a 1970 packet. Ceiling
# 2100-01-01: past it is a defect, not a packet.
_RECV_TIME_FLOOR = 1577836800
_RECV_TIME_CEILING = 4102444800
# The 0x88 frame carries SNR as a signed byte / 4 and RSSI as a signed byte:
# a value outside these ranges cannot have come off the wire.
_SNR_MIN, _SNR_MAX = -32, 32        # -32 <= snr < 32
_RSSI_MIN, _RSSI_MAX = -128, 127


def is_valid_iata(iata: Any) -> bool:
    return isinstance(iata, str) and _IATA.fullmatch(iata) is not None


def _is_number(v: Any) -> bool:
    """int or float, never bool (``True`` is an int in Python).

    No separate finite check: every number is range-checked below, and a
    chained comparison is False for NaN and +/-inf — while
    ``math.isfinite(10**400)`` RAISES (Fable r2 L3)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def build_packet_message(payload: Dict[str, Any], *, iata: str,
                         observer_key: str,
                         origin: Optional[str] = None) -> Union[PacketMessage, Refused]:
    """Translate, or refuse with a named reason. Check order is the table's
    row order (first refusal wins). An optional field ABSENT is omitted; one
    PRESENT but garbage refuses the packet — so an accepted body is always
    strict JSON (``allow_nan=False``) and a refusal is the only way garbage
    leaves."""
    if not isinstance(payload, dict):
        return Refused("not_a_dict")
    if not is_valid_iata(iata):
        return Refused("bad_iata")
    if not isinstance(observer_key, str) or not _OBSERVER_KEY.fullmatch(observer_key):
        return Refused("bad_observer_key")
    packet = payload.get("payload")
    if packet is None or packet == "":
        return Refused("no_packet")
    if not isinstance(packet, str) or not _PACKET_HEX.fullmatch(packet):
        return Refused("not_hex")
    recv_time = payload.get("recv_time")
    if recv_time is None:
        return Refused("no_recv_time")
    if (not _is_number(recv_time)
            or not _RECV_TIME_FLOOR <= recv_time < _RECV_TIME_CEILING):
        return Refused("bad_recv_time")
    snr = payload.get("snr")
    if snr is not None and (not _is_number(snr)
                            or not _SNR_MIN <= snr < _SNR_MAX):
        return Refused("bad_snr")
    rssi = payload.get("rssi")
    if rssi is not None and (not isinstance(rssi, int) or isinstance(rssi, bool)
                             or not _RSSI_MIN <= rssi <= _RSSI_MAX):
        return Refused("bad_rssi")
    if origin is not None and not isinstance(origin, str):
        return Refused("bad_origin")

    received = datetime.fromtimestamp(recv_time, tz=timezone.utc)
    body = {
        "raw": packet,
        "timestamp": received.isoformat(),
    }
    # Optional for CoreScope; absent only on a frame too short to carry them.
    if snr is not None:
        body["SNR"] = snr
    if rssi is not None:
        body["RSSI"] = rssi
    if origin:
        body["origin"] = origin
    return PacketMessage(topic=f"meshcore/{iata}/{observer_key}/packets", body=body)


# MeshCore companion: 0x88 is pushed only if ``len + 3 <= MAX_FRAME_SIZE``
# (``MAX_FRAME_SIZE`` 176; ``examples/companion_radio/MyMesh.cpp:292`` @
# 75b04ef0). Longer on-air packets (up to MAX_TRANS_UNIT 255) never reach us
# and nothing counts them — this observer is blind above this size.
MAX_FRAME_SIZE = 176
UNOBSERVABLE_OVER_BYTES = MAX_FRAME_SIZE - 3


# Length buckets (bytes, inclusive); the last one ends AT the ceiling, so its
# count is how close traffic comes to the blind spot.
LENGTH_BUCKETS = ((1, 32), (33, 64), (65, 96), (97, 128), (129, 160),
                  (161, UNOBSERVABLE_OVER_BYTES))


def _bucket(length: int) -> Optional[str]:
    for lo, hi in LENGTH_BUCKETS:
        if lo <= length <= hi:
            return f"{lo}-{hi}"
    return None


# --- witness state: ONE derivation over the whole input space ---------------
# Table v2 (after Fable r2). First matching row wins; counters never decide
# state. Every input is read LIVE by the caller at snapshot time, or is bound
# to the radio session it was taken on. The 200-row enumeration lives in
# tests/test_meshcore_observer_state.py.
_ENABLED = (False, True)
MODES = ("handler", "supervisor")
RADIOS = ("up", "down")
SETUPS = ("not_attempted", "no_client", "client_raised", "subscribe_raised", "ok")
OUTCOMES = ("none", "queued", "refused", "publish_failed", "internal_error")
_SETUP_FAULT = {"not_attempted": "not_subscribed", "no_client": "no_mqtt_client",
                "client_raised": "client_error", "subscribe_raised": "subscribe_failed"}
_RUNTIME = {"none": "never", "queued": "ok", "refused": "refusing",
            "publish_failed": "failing", "internal_error": "failing"}
RUNTIME_STATES = frozenset(_RUNTIME.values())
# One log line per kind per window (Fable r3 F3); the rest are counted.
LOG_WINDOW_S = 300.0
# never and ok are ONE journal key: a first packet is not a "recovery".
JOURNAL_HEALTHY = "healthy:"
JOURNAL_DISABLED = "disabled:"


def derive_state(*, enabled: bool, mode: str, radio: str, setup: str,
                 last_outcome: str) -> str:
    """The observer's state. Raises ValueError outside the vocabulary (hfm #7)."""
    if enabled is not True and enabled is not False:
        raise ValueError(f"observer state: enabled={enabled!r} is not a bool")
    for name, value, vocab in (("mode", mode, MODES), ("radio", radio, RADIOS),
                               ("setup", setup, SETUPS),
                               ("last_outcome", last_outcome, OUTCOMES)):
        if value not in vocab:
            raise ValueError(f"observer state: {name}={value!r} not in {vocab}")
    if not enabled:
        return "disabled"
    if mode == "supervisor":
        return "unsupported_supervisor_mode"
    if radio == "down":
        return "no_radio"
    if setup != "ok":
        return _SETUP_FAULT[setup]
    return _RUNTIME[last_outcome]


def observer_enabled(meshcore_cfg: Any) -> bool:
    """LIVE read, strict: only a real ``True`` enables (both handlers)."""
    return getattr(meshcore_cfg, "observer_enabled", False) is True


def observer_config_error(meshcore_cfg: Any) -> Optional[str]:
    """Why a configured ``observer_enabled`` was not a JSON bool (D5), or None."""
    errors = getattr(meshcore_cfg, "_config_errors", None)
    return errors.get("observer_enabled") if isinstance(errors, dict) else None


def disabled_snapshot(meshcore_cfg: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"state": "disabled"}
    err = observer_config_error(meshcore_cfg)
    if err is not None:
        out["config_error"] = err
    return out


def _describe(error: BaseException) -> str:
    """``Type: message``, never raising: an exception whose ``__str__`` raises
    must not escape into the radio loop from inside the witness (Fable r6
    INFO-4: the f-string tore the record and raised)."""
    try:
        return f"{type(error).__name__}: {error}"
    except Exception:
        return f"{type(error).__name__}: <unprintable>"


class ObserverWitness:
    """Counters a probe or the TUI reads. Monotonic clock (hfm #6).

    One lock over every record and the snapshot: a reader on another thread
    never sees ``queued`` and ``length_buckets`` from different moments.
    ``queued`` = paho accepted it (rc 0), NOT delivered: the delivered end is
    a stored packet in CoreScope.
    """

    def __init__(self, clock):
        self._clock = clock
        self._lock = threading.Lock()
        self._init_record()

    def reset(self) -> None:
        """Fresh record IN PLACE (v4, r4 L3): a disable keeps the session's
        subscription, so its callback still holds THIS witness — dropping it
        would make those packets vanish uncounted."""
        with self._lock:
            self._init_record()

    def _init_record(self) -> None:   # caller holds the lock (or __init__)
        self._queued = 0
        self._buckets = {f"{lo}-{hi}": 0 for lo, hi in LENGTH_BUCKETS}
        self._refused: Dict[str, int] = {}
        self._publish_failed = 0
        self._internal_errors = 0
        self._last_error = None
        self._last_refused = None
        self._last_outcome = "none"
        self._run = 0
        self._last_queued_at = None
        self._ceiling_contradicted = 0
        self._dropped: Dict[str, int] = {}
        self._last_logged: Dict[str, float] = {}
        self._suppressed = 0
        self._logged_key = JOURNAL_HEALTHY   # the "enabled" INFO line said this
        self._pending_key: Optional[str] = None

    @property
    def last_outcome(self) -> str:
        with self._lock:
            return self._last_outcome

    def record_dropped(self, cause: str) -> None:
        """A packet heard while NOT live (v3): never translated or published,
        never an outcome — counted by its CAUSE (v4, r4 L2): ``stale_session``,
        ``disabled``, or the current session's non-runtime state."""
        with self._lock:
            self._dropped[cause] = self._dropped.get(cause, 0) + 1

    def dropped(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._dropped)

    def _window_open(self, kind: str) -> bool:   # caller holds the lock
        now = self._clock()
        last = self._last_logged.get(kind)
        # a clock that went backward reopens the window (hfm #6)
        if last is None or now - last >= LOG_WINDOW_S or now < last:
            self._last_logged[kind] = now
            return True
        return False

    def journal_logged(self, key: str) -> None:
        """A line the handler wrote OUTSIDE the packet path (the init
        ``enabled`` / ``disabled`` lines) is a journal line too: record it as
        the last logged key, or the journal can end on it while the state is
        failing (Fable r5 MED-1)."""
        with self._lock:
            # the key only: an init line is not a rate-limited packet-path
            # line, so it must not use up that key's window (a real
            # "recovered" shortly after a connect would be held back)
            self._logged_key = key
            self._pending_key = None

    def journal_owes(self, key: str) -> bool:
        """The journal is a projection of the DERIVED STATE (v4, r4 M1): a
        line is owed whenever ``key`` (state:detail) differs from the last
        LOGGED key. Rate-limited per key; a change that cannot be emitted yet
        is counted ONCE and stays pending, so the next call after its window
        reopens emits it — the journal disagrees with the state for at most
        one window, never forever."""
        with self._lock:
            if key == self._logged_key:
                self._pending_key = None
                return False
            if self._window_open(key):
                self._logged_key = key
                self._pending_key = None
                return True
            if self._pending_key != key:
                self._suppressed += 1
                self._pending_key = key
            return False

    def _outcome(self, outcome: str) -> str:   # caller holds the lock
        """Record the outcome and its run length; return the previous one."""
        prev = self._last_outcome
        self._run = self._run + 1 if outcome == prev else 1
        self._last_outcome = outcome
        return prev

    def record_queued(self, msg: PacketMessage) -> str:
        key = _bucket(len(bytes.fromhex(msg.body["raw"])))
        with self._lock:
            self._queued += 1
            self._last_queued_at = self._clock()
            self._last_error = None
            if key is None:
                self._ceiling_contradicted += 1
            else:
                self._buckets[key] += 1
            return self._outcome("queued")

    def record_refused(self, refused: Refused) -> str:
        with self._lock:
            self._refused[refused.reason] = self._refused.get(refused.reason, 0) + 1
            self._last_refused = refused.reason
            return self._outcome("refused")

    def record_publish_failed(self, msg: PacketMessage, error: BaseException) -> str:
        with self._lock:
            self._publish_failed += 1
            self._last_error = _describe(error)
            return self._outcome("publish_failed")

    def record_internal_error(self, error: BaseException) -> str:
        with self._lock:
            self._internal_errors += 1
            self._last_error = f"internal {_describe(error)}"
            return self._outcome("internal_error")

    def snapshot(self) -> Dict[str, Any]:
        """Counters + the runtime state as if setup were healthy; the handler
        re-derives ``state`` with its setup facts (``derive_state``)."""
        with self._lock:
            return {
                "state": _RUNTIME[self._last_outcome],
                "last_outcome": self._last_outcome,
                "last_outcome_run": self._run,
                "queued": self._queued,
                "length_buckets": dict(self._buckets),
                "refused": dict(self._refused),
                "last_refused": self._last_refused,
                "publish_failed": self._publish_failed,
                "internal_errors": self._internal_errors,
                "last_error": self._last_error,
                "last_queued_age_s": (None if self._last_queued_at is None
                                      else self._clock() - self._last_queued_at),
                "unobservable_over_bytes": UNOBSERVABLE_OVER_BYTES,
                "ceiling_contradicted": self._ceiling_contradicted,
                "dropped_not_live": dict(self._dropped),
                "log_lines_suppressed": self._suppressed,
            }


class LocalBrokerPublisher:
    """JSON at QoS 0, never retained (meshcoretomqtt's packet contract).

    A non-success rc (not connected, queue full) RAISES so the caller's
    witness counts it — paho's publish otherwise fails quietly.
    """

    def __init__(self, client: Any):
        self._client = client

    def publish(self, topic: str, body: Dict[str, Any]) -> None:
        info = self._client.publish(topic, json.dumps(body), qos=0, retain=False)
        rc = getattr(info, "rc", None)
        if rc != 0:
            raise ConnectionError(f"MQTT publish rc={rc}")


def make_local_publisher(host: str = "127.0.0.1", port: int = 1883) -> Optional[LocalBrokerPublisher]:
    """A paho client on the local broker, connecting in the background.

    None when paho-mqtt is not installed: the caller reports no_mqtt_client.
    """
    mqtt = _mqtt
    if mqtt is None:
        return None
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="meshanchor-meshcore-observer")
    client.connect_async(host, port, keepalive=60)
    client.loop_start()
    return LocalBrokerPublisher(client)
