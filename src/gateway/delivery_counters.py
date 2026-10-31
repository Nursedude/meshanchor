"""Honest delivery lifecycle counters — Fork C of the federation→DB
pressure→wedge stability arc.

The existing ``PersistentMessageQueue._stats`` tracks queue *operations*
(``enqueued`` / ``delivered`` / ``failed`` / ``retried`` / ``shed`` /
``deduplicated``), which conflates two things operators actually want
separated:

* **Sent** — message was handed off to the destination network. For RNS
  this is ``LXMRouter.handle_outbound`` returning without raising; for
  Meshtastic this is the toRadio POST succeeding. Today's
  ``_stats["delivered"]`` measures this — but the name is misleading
  because actual end-to-end delivery is not yet known.
* **Confirmed** — the *receiver* signaled receipt. For RNS this is the
  ``register_delivery_callback`` firing (LXMF delivery proof); for
  Meshtastic this is the wantAck path returning a proof. The bridge
  tracks this via ``bridge_health.DeliveryTracker.confirm_delivery``
  but never aggregates it into the queue-level stats. So
  ``_stats["delivered"]`` is structurally optimistic.

Fork C adds a parallel set of counters that *don't* conflate the two,
indexed by a single ``DeliveryState`` enum and a ``DropReason``
taxonomy. Each lifecycle event carries the message id (CanonicalMessage
.id when available, the queue's auto-generated id otherwise) so an
operator following one message can find every counter bump it caused.

Storage: SQLite-backed at
``~/.local/share/meshanchor/delivery_counters.db`` (WAL via
``utils.db_helpers.connect_tuned``, DBSpec entry in
``utils.db_inventory``). The gateway daemon writes; the map daemon
serves ``/api/gateway/delivery`` from a read of the same file. This
closes the per-process gap that an in-memory singleton can't bridge
without losing the architectural separation between the two daemons.
Counters become true monotonic across the gateway's lifetime — restart
no longer zeroes the operator's view. The events ring is FIFO-bounded
at ``RING_BUFFER_CAP`` rows via prune-on-insert.

Scope: this module is **observability only** — it does not change
control flow, never validates state transitions, never raises. A
caller that bumps ``confirmed`` without an earlier ``sent`` is fine
from this module's perspective; the queue's lifecycle history table
(``record_lifecycle_event``) is the validator. These counters are the
operator-facing aggregate.

Wiring: see ``record(state, msg_id, ...)`` call sites in
``message_queue.py`` (queue transitions) and ``rns_bridge.py`` (LXMF
delivery callbacks).

Surface: ``snapshot()`` returns a JSON-serializable dict suitable for
splicing into ``/api/status.delivery`` or a dedicated
``/api/gateway/delivery`` endpoint. Operators read it; nothing else
depends on its shape.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gateway import delivery_legs
from utils.db_helpers import connect_tuned
from utils.delivery_labels import canonical_protocol, lane_of
from utils.paths import get_real_user_home


logger = logging.getLogger(__name__)


# ── State + reason taxonomy ──────────────────────────────────────────


class DeliveryState(Enum):
    """The four states a message visits in its delivery lifecycle.

    Deliberately coarse — the queue's ``MessageLifecycleState`` keeps
    the fine-grained CREATED/QUEUED/SENT/RELAYED/DELIVERED/ACK trace.
    These four are the operator-relevant rollups.
    """

    QUEUED = "queued"
    """Message entered the persistent queue. Includes initial enqueue
    plus re-queue after a transient failure (a single message can
    transition into QUEUED multiple times; the counter is per-event,
    not per-id)."""

    SENT = "sent"
    """Handed off to the destination network. Subprocess / RPC returned
    success; the network has the bytes. Does NOT imply the receiver
    got them."""

    CONFIRMED = "confirmed"
    """Receiver confirmed receipt — LXMF delivery proof, Meshtastic
    wantAck proof, MQTT broker QoS-1 PUBACK. Final positive outcome."""

    DROPPED = "dropped"
    """Final negative outcome. Always carries a ``DropReason`` so the
    operator can answer "why did we lose this message?" without
    digging into logs."""


class DropReason(Enum):
    """Why a message reached the DROPPED terminal state.

    Intentionally a closed taxonomy — adding a new reason is a
    deliberate code change, not an ad-hoc string. New entries should
    name the *cause* (not the symptom): ``CIRCUIT_OPEN`` is the
    circuit breaker's decision; ``DESTINATION_UNREACHABLE`` is the
    routing layer's decision; both might apply to the same outage but
    they're separate stories.
    """

    DEDUP = "dedup"
    QUEUE_PRESSURE = "queue_pressure"
    QUEUE_SHED = "queue_shed"
    RETRIES_EXHAUSTED = "retries_exhausted"
    NON_RETRIABLE_ERROR = "non_retriable_error"
    CIRCUIT_OPEN = "circuit_open"
    WEDGED = "wedged"
    DESTINATION_UNREACHABLE = "destination_unreachable"
    RNS_DELIVERY_FAILED = "rns_delivery_failed"
    DELIVERY_TIMEOUT = "delivery_timeout"
    EVICTED_OVERFLOW = "evicted_overflow"
    INVALID_PAYLOAD = "invalid_payload"
    # 2026-10-24 (operator): the destination's own gate refused a message the
    # gateway had ACCEPTED, as not sendable as addressed (MeshCore: no slot
    # 0..255). It is lost, so it is a delivery failure — unlike
    # INVALID_PAYLOAD, which stays benign. MeshForge carries the same member.
    UNSENDABLE = "unsendable"
    # ROADMAP 6f (MF lead): a queued row outlived its max age undelivered.
    EXPIRED = "expired"
    # 2026-10-29 (MF BIC-2 gate 3, review #4, ported): the RNS→RF ingress
    # gate refused a message LXMF had ALREADY proven delivered to the
    # gateway. A policy decision — never in DELIVERY_FAILURE_REASONS, never
    # in the terminal ring — counted as its own leg outcome, ``refused``.
    INGRESS_REFUSED = "ingress_refused"
    UNKNOWN = "unknown"


# ── Event record ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class DeliveryEvent:
    """One transition observation.

    ``id`` is whatever string the caller has at the call site: the
    queue's auto-generated msg_id, the CanonicalMessage.id (uuid4),
    or — for bare-protocol paths that don't use either — a synthesized
    "lxmf-<ts>" / similar.

    ``protocol`` is the destination network ("meshtastic" / "rns" /
    "meshcore" / "mqtt"). None for transitions that don't have one
    (a dedup drop at enqueue may not know the protocol).

    ``drop_reason`` is required when ``state == DROPPED``. The
    constructor accepts it for any state — non-drop states ignore it.

    ``note`` is operator-friendly free text (e.g. "rc=1 unit not
    found", "circuit half-open"). Kept short — this rides in the
    events table that operators scrape.
    """

    ts: float
    id: str
    state: DeliveryState
    protocol: Optional[str] = None
    drop_reason: Optional[DropReason] = None
    note: str = ""
    # BIC-2 (MeshForge twin): the transport the message came FROM; with
    # ``protocol`` it names the leg (``source>protocol``). None = the call
    # site did not say.
    source: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "ts": self.ts,
            "id": self.id,
            "state": self.state.value,
            "protocol": self.protocol,
            "drop_reason": self.drop_reason.value if self.drop_reason else None,
        }
        if self.source:
            d["source"] = self.source
        if self.note:
            d["note"] = self.note
        return d


# ── Defaults ─────────────────────────────────────────────────────────


# Terminal DELIVERY-failure reasons — the denominator's failure half for the
# honest confirmation view. Deliberately excludes benign pre-delivery drops
# (dedup, queue_pressure, queue_shed, evicted_overflow; invalid_payload —
# no producer since 2026-10-24, the slot gates moved to UNSENDABLE):
# those messages never reached a delivery attempt, so counting them would
# read as delivery failure. PORT of MeshForge's #74 display cure (twin file,
# UNTRACKED-diverged tier — keep the reason set aligned when either side
# grows a DropReason). Consumers import it rather than re-listing it
# (honest_failure_modes #5 — two independent hardcodes WILL drift).
DELIVERY_FAILURE_REASONS = frozenset({
    DropReason.RNS_DELIVERY_FAILED.value,
    DropReason.RETRIES_EXHAUSTED.value,
    DropReason.DESTINATION_UNREACHABLE.value,
    DropReason.DELIVERY_TIMEOUT.value,
    DropReason.NON_RETRIABLE_ERROR.value,
    DropReason.CIRCUIT_OPEN.value,
    DropReason.WEDGED.value,
    DropReason.EXPIRED.value,
    DropReason.UNSENDABLE.value,
})


def _add_column(conn: sqlite3.Connection, table: str, col: str) -> None:
    """Additive ``TEXT NOT NULL DEFAULT ''`` column; a lost race is not an error.

    PRAGMA-then-ALTER is not atomic: the gateway and the map daemon migrate
    the same DB together on a deploy, and the loser's ALTER raises
    "duplicate column name" out of the constructor (review F1, 10-23).
    Any OTHER error still raises."""
    try:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e):
            raise


def _leg_outcome(event: "DeliveryEvent") -> str:
    """The ``delivery_legs.OUTCOMES`` word for one event."""
    if event.state != DeliveryState.DROPPED:
        return event.state.value
    if event.drop_reason == DropReason.INGRESS_REFUSED:
        return "refused"
    if event.drop_reason and event.drop_reason.value in DELIVERY_FAILURE_REASONS:
        return "failed"
    return "dropped_other"


def compute_confirmation_view(
    state_totals: Dict[str, int],
    state_by_protocol: Dict[str, Dict[str, int]],
    drop_reasons: Dict[str, int],
    drop_reasons_by_protocol: Optional[Dict[str, Dict[str, int]]] = None,
) -> Dict[str, Any]:
    """Honest confirmation accounting from raw counters (MF Issue #74 port,
    2026-07-19 — the pre-port ``confirmed / sent`` read 7.625 = ">762%
    confirmed" LIVE on meshanchor-server, caught by honest_status's
    conf_rate<=1.0 leg the first time it could verify).

    ``confirmed / sent`` is a CROSS-POPULATION ratio: ``confirmed`` counts
    terminal outcomes over the whole history while ``sent`` is the CURRENT
    in-flight count — the quotient is unbounded and a valid-looking value
    masks the real question (of the messages that reached a terminal
    delivery outcome, how many confirmed?).

    Honest replacement (identical semantics to the MF twin):
      - ``confirmation_rate`` = confirmed / (confirmed + delivery-failures)
        — bounded [0,1], None when no confirmable terminal events yet ("no
        data" must not read as 0% = total failure).
      - ``unconfirmable_sent`` = in-flight sends on protocols that have
        never recorded a confirmation — the blind spot surfaced as its own
        number, never averaged into a healthy-looking scalar.
      - ``confirmable_protocols`` = what the rate actually covers.
    """
    def _pos_int(v) -> int:
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    confirmed = _pos_int((state_totals or {}).get(DeliveryState.CONFIRMED.value, 0))
    confirmed_by_proto = (state_by_protocol or {}).get(
        DeliveryState.CONFIRMED.value, {}) or {}
    sent_by_proto = (state_by_protocol or {}).get(
        DeliveryState.SENT.value, {}) or {}

    confirmable = sorted(p for p, c in confirmed_by_proto.items() if _pos_int(c) > 0)
    confirmable_set = set(confirmable)
    global_failures = {
        r: _pos_int((drop_reasons or {}).get(r, 0)) for r in DELIVERY_FAILURE_REASONS
    }
    if drop_reasons_by_protocol and confirmable_set:
        # Judge only protocols that can confirm: a failure on one that never
        # confirms (MeshCore channel sends) has no CONFIRMED counterpart, so
        # it is the blind spot, surfaced separately — not the denominator.
        # Drops recorded before drop_proto.* keys existed (2026-09-23) carry
        # no protocol and STAY in the denominator: forgiving them would pair
        # lifetime confirmations with post-patch failures only (1 MeshCore
        # drop read 1.0 over real RNS failures). Unattributed = global
        # minus what is attributed; a protocol-less drop lands there too.
        attributed = dict.fromkeys(DELIVERY_FAILURE_REASONS, 0)
        failures = 0
        for p, reasons in drop_reasons_by_protocol.items():
            for r, v in (reasons or {}).items():
                if r in attributed:
                    attributed[r] += _pos_int(v)
                    if p in confirmable_set:
                        failures += _pos_int(v)
        failures += sum(max(0, global_failures[r] - attributed[r])
                        for r in DELIVERY_FAILURE_REASONS)
    else:
        # No per-protocol keys yet, or nothing has confirmed: the global sum,
        # so "attempted and failed, none confirmed" still reads 0.0.
        failures = sum(global_failures.values())
    terminal = confirmed + failures
    rate = confirmed / terminal if terminal > 0 else None
    unconfirmable_sent = sum(
        _pos_int(v) for p, v in sent_by_proto.items() if p not in confirmable_set
    )
    return {
        "confirmation_rate": rate,
        "confirmable_protocols": confirmable,
        "unconfirmable_sent": unconfirmable_sent,
    }


RING_BUFFER_CAP = 500
# Ingress refusals keep their OWN pool in the events table, pruned apart
# from the delivery rows (re-review 2026-10-29 #1): a stranger flood under
# one shared cap deleted the confirmed terminals the stall probe judges.
# RING_BUFFER_CAP bounds the delivery rows alone; the table holds at most
# the sum. The exact refusal COUNT lives in the leg buckets, not here.
REFUSAL_RING_CAP = 50
"""Maximum rows retained in the events table. Sized for ~1 hour of
healthy gateway traffic on a 5-box fleet (a few dozen msgs/min peak);
enough forensic context for incident investigation without bloating
the DB. Per-id history rolls into the queue's durable
``message_lifecycle`` SQLite table — this events table is the fast
"what just happened?" view for ``/api/gateway/delivery``.

Pruned on insert via ``DELETE FROM events WHERE rowid IN (oldest)``,
so the cap is enforced even when the gateway runs for weeks."""


SNAPSHOT_RECENT_LIMIT = 200
"""Events served in the snapshot's ``recent`` ring (must stay ≤
``RING_BUFFER_CAP``). Was 50 until 2026-08-10 (MF-parity port): on a
mesh-heavy gateway a 50-event ring holds fewer confirmable terminal
events than ``check_delivery_confirmation_stall``'s ``min_terminal=20``
sample floor, so the stall check sat permanently in ``low_traffic`` —
measured on MF's moc (~30% confirmable-terminal ring density, blind
flapping 07-26→08-10). At 200 the same density yields ~60 judgeable
events. A cross-constant test pins the ratio to the probe's floor
(honest_failure_modes #5)."""


def default_db_path() -> Path:
    """Resolve the counters DB path.

    Env var ``MESHANCHOR_DELIVERY_COUNTERS_DB`` wins (test seam; also
    useful for ops who want the DB on a different volume). Otherwise
    matches the standard MeshForge data dir layout per
    ``utils.db_inventory._meshanchor_data_dir``.
    """
    override = os.environ.get("MESHANCHOR_DELIVERY_COUNTERS_DB")
    if override:
        return Path(override)
    return get_real_user_home() / ".local" / "share" / "meshanchor" / "delivery_counters.db"


# ── Counters (DB-backed) ─────────────────────────────────────────────


class DeliveryCounters:
    """Cross-process delivery lifecycle counters + events ring.

    SQLite-backed at ``db_path``. The gateway daemon and map daemon
    point at the same file: gateway writes, map reads. Counters are
    monotonic across process lifecycle — restart no longer zeroes the
    operator's view.

    Two tables:

    * ``counters`` — KV pairs. State totals
      (``state.<value>``), drop-reason histogram
      (``drop.<value>``), per-protocol breakdown
      (``state_proto.<state>.<protocol>``), first/last event ts
      (``meta.first_event_ts``, ``meta.last_event_ts``). KV shape
      keeps the schema sparse — a protocol we've never seen doesn't
      occupy a row.
    * ``events`` — FIFO ring. INTEGER PRIMARY KEY rowid orders
      chronologically; prune-on-insert keeps row count at
      ``ring_cap``.

    Concurrency: a per-instance ``threading.Lock`` serializes writes
    within one process. SQLite's WAL mode + per-connection
    ``busy_timeout`` (30 s from ``connect_tuned``) handles cross-
    process contention between the gateway writer and the map reader.

    Each ``record()`` opens + closes a connection (cheap on a WAL DB).
    The alternative — a long-lived connection — would require
    ``check_same_thread=False`` plus an explicit GIL-aware locking
    discipline; the open-per-call pattern matches the rest of
    MeshForge (PersistentMessageQueue, NodeHistoryDB).
    """

    def __init__(
        self,
        *,
        db_path: Optional[Path] = None,
        ring_cap: int = RING_BUFFER_CAP,
        run_preflight: bool = True,
    ) -> None:
        self._db_path = db_path or default_db_path()
        self._ring_cap = int(ring_cap)
        self._lock = threading.Lock()
        # Write-path health (Issue #63 / reliability backlog #2).
        # `preflight_ok` reflects the at-construction write+read canary;
        # the `_write_error_*` fields track runtime regressions caught
        # during real record() calls. Surfaced via snapshot().health
        # so /api/gateway/delivery shows "writes are broken" without
        # operators having to grep journald.
        self._preflight_ok: bool = False
        self._preflight_ts: Optional[float] = None
        self._preflight_error: Optional[str] = None
        self._consecutive_write_errors: int = 0
        self._last_write_error_ts: Optional[float] = None
        self._last_write_error: Optional[str] = None
        self._init_db()
        if run_preflight:
            self._run_preflight()

    # ── schema ──────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create the schema. Idempotent; safe to call from multiple
        processes (CREATE TABLE IF NOT EXISTS guards both sides)."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS counters ("
                " key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                " ts REAL NOT NULL,"
                " id TEXT NOT NULL DEFAULT '',"
                " state TEXT NOT NULL,"
                " protocol TEXT,"
                " drop_reason TEXT,"
                " note TEXT NOT NULL DEFAULT '',"
                # BIC-2: the leg's origin transport, for the per-message trace.
                " source TEXT NOT NULL DEFAULT ''"
                ")"
            )
            # A DB created before BIC-2 has no source column (CREATE TABLE IF
            # NOT EXISTS is a no-op when the table exists, so it needs ALTER).
            existing = {row[1] for row in
                        conn.execute("PRAGMA table_info(events)")}
            if "source" not in existing:
                _add_column(conn, "events", "source")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_id ON events(id)"
            )
            delivery_legs.init_schema(conn)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        """Open a tuned connection. Caller owns lifecycle."""
        return connect_tuned(self._db_path)

    # ── preflight / health canary (Issue #63) ──────────────────────────

    def _run_preflight(self) -> None:
        """One-shot write+read at construction. Catches the Issue #58
        class — sandbox path drift, schema corruption, disk full, or any
        other write-path failure — before natural traffic flows hours
        later.

        Persists `meta.preflight_ts` (ms) and `meta.preflight_ok` (1/0)
        in the counters table so a reader (the map daemon) can include
        them in its `/api/gateway/delivery.health` snapshot. The writer
        ALSO holds the result in instance state for ERROR-level logging.

        Failure is NOT fatal — the gateway keeps running; observability
        just becomes degraded. Sandbox-path drift was already cured
        loudly in Issue #60 (`assert_writable_or_exit`); this is a
        defense-in-depth for the silent-failure classes that don't trip
        `assert_writable_or_exit` (e.g. mid-run permission change, disk
        full, schema corruption from an aborted upgrade).
        """
        canary_ts = time.time()
        canary_ms = int(canary_ts * 1000)
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO counters(key, value) "
                    "VALUES('meta.preflight_ts', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (canary_ms,),
                )
                conn.execute(
                    "INSERT INTO counters(key, value) "
                    "VALUES('meta.preflight_ok', 1) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                )
                conn.commit()
                # Read back to confirm — catches the (rare) class where
                # the write returns success but the value doesn't land
                # (e.g. a stale WAL on a read-only mount).
                row = conn.execute(
                    "SELECT value FROM counters WHERE key = 'meta.preflight_ts'"
                ).fetchone()
                if row is None or row[0] != canary_ms:
                    raise RuntimeError(
                        f"readback mismatch: wrote={canary_ms}, got={row}"
                    )
            self._preflight_ok = True
            self._preflight_ts = canary_ts
            self._preflight_error = None
            logger.info(
                "delivery_counters preflight OK at %s", self._db_path,
            )
        except (sqlite3.Error, OSError, RuntimeError) as e:
            # Best-effort: persist the FAILED preflight state too, so the
            # map daemon's snapshot reflects reality. If even THIS write
            # fails (worst case: read-only filesystem), the local
            # instance state + ERROR log are the canonical signals.
            try:
                with self._lock, self._connect() as conn:
                    conn.execute(
                        "INSERT INTO counters(key, value) "
                        "VALUES('meta.preflight_ts', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (canary_ms,),
                    )
                    conn.execute(
                        "INSERT INTO counters(key, value) "
                        "VALUES('meta.preflight_ok', 0) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    )
                    conn.commit()
            except Exception:  # pragma: no cover — degenerate case
                pass
            self._preflight_ok = False
            self._preflight_ts = canary_ts
            self._preflight_error = str(e)
            logger.error(
                "delivery_counters preflight FAILED at %s: %s — "
                "operator visibility into delivery events will silently "
                "degrade until fixed. Check the database path, "
                "permissions, and disk space.",
                self._db_path, e,
            )

    # ── ingest ──────────────────────────────────────────────────

    def record(
        self,
        state: DeliveryState,
        msg_id: str,
        *,
        protocol: Optional[str] = None,
        drop_reason: Optional[DropReason] = None,
        note: str = "",
        ts: Optional[float] = None,
        source: Optional[str] = None,
    ) -> DeliveryEvent:
        """Record one lifecycle transition.

        Never raises (silently coerces unknown enums, swallows DB
        errors after logging) — the gateway's hot paths must not
        crash because a counter call site has a typo or the disk
        is full.

        Returns the constructed event so callers can inspect it in
        tests without a second timestamp call.
        """
        if not isinstance(state, DeliveryState):
            logger.warning(
                "delivery_counters.record: bad state %r — ignoring", state,
            )
            return DeliveryEvent(
                ts=ts if ts is not None else time.time(),
                id=str(msg_id) if msg_id is not None else "",
                state=DeliveryState.DROPPED,
                drop_reason=DropReason.UNKNOWN,
                note=f"bad_state:{state!r}",
            )
        if drop_reason is not None and not isinstance(drop_reason, DropReason):
            logger.warning(
                "delivery_counters.record: bad drop_reason %r — coercing to UNKNOWN",
                drop_reason,
            )
            drop_reason = DropReason.UNKNOWN
        if state == DeliveryState.DROPPED and drop_reason is None:
            drop_reason = DropReason.UNKNOWN
        # A routing lane (dual-radio primary/secondary) is not a transport:
        # count it under the transport, keep the lane in the note (review C F7).
        lane = lane_of(protocol)
        protocol = canonical_protocol(protocol)
        if lane:
            note = f"lane={lane} {note}".strip()

        event = DeliveryEvent(
            ts=ts if ts is not None else time.time(),
            id=str(msg_id) if msg_id is not None else "",
            state=state,
            protocol=protocol,
            drop_reason=drop_reason,
            note=note,
            source=canonical_protocol(source) or None,
        )

        try:
            self._persist(event)
            # Successful write → clear runtime error state. If we had
            # been failing, log the recovery so it's visible without
            # operators having to diff timestamps.
            with self._lock:
                prior_errors = self._consecutive_write_errors
                if prior_errors > 0:
                    logger.info(
                        "delivery_counters: write-path recovered after "
                        "%d consecutive errors", prior_errors,
                    )
                self._consecutive_write_errors = 0
                self._last_write_error = None
                self._last_write_error_ts = None
            if prior_errors > 0:
                # Clear the persisted count too so cross-process
                # readers see the recovery (Issue #74).
                self._persist_write_error_state(0, None)
        except sqlite3.Error as e:
            with self._lock:
                self._consecutive_write_errors += 1
                self._last_write_error_ts = time.time()
                self._last_write_error = str(e)
                error_count = self._consecutive_write_errors
                error_ts_ms = round(self._last_write_error_ts * 1000)
            # First failure logs ERROR (operator-visible). Subsequent
            # failures throttle to DEBUG to avoid flooding journald —
            # snapshot.health.consecutive_write_errors keeps the count.
            if error_count == 1:
                logger.error(
                    "delivery_counters: DB write FAILED (%s) — event "
                    "lost. snapshot.health.last_write_error tracks this; "
                    "next successful write logs recovery.", e,
                )
            else:
                logger.debug(
                    "delivery_counters: write failure #%d: %s",
                    error_count, e,
                )
            self._persist_write_error_state(error_count, error_ts_ms)
        return event

    def _persist_write_error_state(
        self, count: int, ts_ms: Optional[int]
    ) -> None:
        """Best-effort UPSERT of the runtime write-error state (Issue #74).

        Cross-process honesty: the map daemon serves ``snapshot()`` to
        the watchdog's delivery_write_canary probe, but the in-memory
        error counters live in the WRITER (gateway) process — before
        this persist, the probe's degraded branch always read 0 from
        the map daemon's reader instance and could never fire on a
        mid-run write regression. Wrapped in its own try/except so a
        failure here can't recurse or mask the original error (this is
        often called BECAUSE a write just failed — when the DB is fully
        down, the local fields plus ``last_successful_write_ts``
        staleness still carry the signal).
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO counters(key, value) "
                    "VALUES('meta.consecutive_write_errors', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (count,),
                )
                if ts_ms is not None:
                    conn.execute(
                        "INSERT INTO counters(key, value) "
                        "VALUES('meta.last_write_error_ts', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (ts_ms,),
                    )
                else:
                    # Recovery: clear the persisted ts too — the
                    # health contract is "no error state after a
                    # successful write" (see
                    # test_runtime_write_recovery_clears_error_state).
                    conn.execute(
                        "DELETE FROM counters "
                        "WHERE key = 'meta.last_write_error_ts'"
                    )
                conn.commit()
        except sqlite3.Error as e:
            logger.debug(
                "delivery_counters: could not persist write-error "
                "state (%s) — local fields carry it", e,
            )

    def _persist(self, event: DeliveryEvent) -> None:
        """Single-transaction write: bump counters + insert event +
        prune oldest row(s) if over cap.

        Held under ``self._lock`` so we don't issue overlapping
        UPSERTs from the same process; cross-process serialization
        is SQLite's busy_timeout."""
        with self._lock, self._connect() as conn:
            # Counter bumps via UPSERT. SQLite's ON CONFLICT clause is
            # the idiomatic atomic increment.
            counter_keys: List[str] = [f"state.{event.state.value}"]
            if event.drop_reason is not None:
                counter_keys.append(f"drop.{event.drop_reason.value}")
            if event.protocol:
                counter_keys.append(
                    f"state_proto.{event.state.value}.{event.protocol}"
                )
                if event.drop_reason is not None:
                    # Per-protocol drop reasons: the confirmation view must
                    # count failures only on protocols that can confirm
                    # (reviewer probe P2, 2026-09-23 — MeshCore drops moved
                    # the RNS-only rate 1.0 -> 0.5).
                    counter_keys.append(
                        f"drop_proto.{event.drop_reason.value}.{event.protocol}"
                    )
            for key in counter_keys:
                conn.execute(
                    "INSERT INTO counters(key, value) VALUES(?, 1) "
                    "ON CONFLICT(key) DO UPDATE SET value = value + 1",
                    (key,),
                )
            # Meta: first/last ts. first_event_ts is set only on first
            # write; last_event_ts is overwritten every time.
            conn.execute(
                "INSERT INTO counters(key, value) VALUES('meta.first_event_ts', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (round(event.ts * 1000),),
            )
            conn.execute(
                "INSERT INTO counters(key, value) VALUES('meta.last_event_ts', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (round(event.ts * 1000),),
            )
            # Event row.
            conn.execute(
                "INSERT INTO events(ts, id, state, protocol, drop_reason, "
                "source, note) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    event.ts,
                    event.id,
                    event.state.value,
                    event.protocol,
                    event.drop_reason.value if event.drop_reason else None,
                    event.source or "",
                    event.note,
                ),
            )
            outcome = _leg_outcome(event)
            leg = delivery_legs.leg_for(event.source, event.protocol, outcome)
            if leg:
                # Fenced in a SAVEPOINT (MeshForge reviews R7, F1/F2): legs
                # are measure-only, so a failure undoes the leg write WHOLE
                # and keeps the core event. If SQLite already rolled back the
                # whole transaction (FULL/IOERR), ROLLBACK TO raises and the
                # core write fails loudly instead of committing a witness
                # over a lost event. The witness is on disk so the map
                # daemon can read it.
                conn.execute("SAVEPOINT legs")
                try:
                    delivery_legs.bump(conn, event.ts, leg, outcome)
                    conn.execute("RELEASE legs")
                except sqlite3.Error as e:
                    if not conn.in_transaction:
                        # SQLite already rolled back the WHOLE transaction:
                        # the core event is gone. Say why — ROLLBACK TO would
                        # raise "no such savepoint" and bury the cause
                        # (review F2, 10-23).
                        raise sqlite3.OperationalError(
                            f"leg write failed ({e}); transaction already "
                            "rolled back, event lost") from e
                    conn.execute("ROLLBACK TO legs")
                    conn.execute("RELEASE legs")
                    logger.warning("delivery_counters: leg count failed: %s", e)
                    conn.execute(
                        "INSERT INTO counters(key, value) "
                        "VALUES('legs.write_errors', 1) "
                        "ON CONFLICT(key) DO UPDATE SET value = value + 1")
                    conn.execute(
                        "INSERT INTO counters(key, value) "
                        "VALUES('legs.last_write_error_ts', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (round(time.time() * 1000),))
            # Ring cap via prune-on-insert. SELECT COUNT(*) is O(N) on
            # an unindexed scan but N is tiny (cap 500), and we only
            # pay it on the writes that are likely to push us over.
            # Two pools, each pruned only by its own inserts: a refusal can
            # never delete a delivery row, nor a delivery row a refusal.
            refused = event.drop_reason == DropReason.INGRESS_REFUSED
            pool = ("drop_reason = ?" if refused else "drop_reason IS NOT ?")
            cap = REFUSAL_RING_CAP if refused else self._ring_cap
            count = conn.execute(
                f"SELECT COUNT(*) FROM events WHERE {pool}",
                (DropReason.INGRESS_REFUSED.value,),
            ).fetchone()[0]
            if count > cap:
                conn.execute(
                    "DELETE FROM events WHERE rowid IN "
                    f"(SELECT rowid FROM events WHERE {pool} "
                    "ORDER BY rowid ASC LIMIT ?)",
                    (DropReason.INGRESS_REFUSED.value, count - cap),
                )
            conn.commit()

    # ── operator surface ───────────────────────────────────────

    def snapshot(
        self, recent_limit: int = SNAPSHOT_RECENT_LIMIT,
    ) -> Dict[str, Any]:
        """Operator-readable aggregate. Same shape as the in-memory
        predecessor — switching backends is invisible at the
        ``/api/gateway/delivery`` boundary.

        Reads happen via the same WAL DB the gateway is writing into;
        SQLite's snapshot isolation gives a consistent view. The
        ``recent_limit`` argument caps the JSON size (default
        ``SNAPSHOT_RECENT_LIMIT``).
        """
        snapshot_failed = False
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT key, value FROM counters"
                ).fetchall()
                events_rows = conn.execute(
                    "SELECT ts, id, state, protocol, drop_reason, source, note "
                    "FROM events ORDER BY rowid DESC "
                    "LIMIT ?",
                    (max(0, recent_limit),),
                ).fetchall()
                # 2026-09-10 (ported from MeshForge 7e26f2fa): TERMINAL events
                # across the WHOLE ring, not just the newest `recent_limit`
                # slots. `recent` is a general-purpose FIFO, so on a gateway
                # whose traffic is mostly a protocol that can never confirm,
                # the confirmable events a judgement needs get evicted by ones
                # it must ignore. Measured on a MeshForge gateway: 200 slots
                # spanning 17.7 h held 196 unconfirmable events beside 4
                # confirmable, against a check needing 20 — so the check could
                # not judge, and a TOTAL confirmation collapse read the same.
                # The same ring held 49 confirmable terminals over 25.9 h.
                terminal_rows = conn.execute(
                    "SELECT ts, id, state, protocol, drop_reason "
                    "FROM events WHERE state IN (?, ?) "
                    # a refusal is policy, not a delivery outcome: strangers
                    # must not evict the terminals the stall probe judges
                    "AND drop_reason IS NOT ? "
                    "ORDER BY rowid DESC LIMIT ?",
                    (DeliveryState.CONFIRMED.value, DeliveryState.DROPPED.value,
                     DropReason.INGRESS_REFUSED.value, max(0, recent_limit)),
                ).fetchall()
                ring_capacity = self._ring_cap
                legs_view = delivery_legs.read(conn, time.time())
        except sqlite3.Error as e:
            logger.warning(
                "delivery_counters: snapshot read failed (%s) — "
                "returning empty defaults", e,
            )
            rows = []
            events_rows = []
            terminal_rows = []
            ring_capacity = self._ring_cap
            legs_view = (None, None, None, None, None)
            snapshot_failed = True

        state_totals: Dict[str, int] = {s.value: 0 for s in DeliveryState}
        drop_reasons: Dict[str, int] = {r.value: 0 for r in DropReason}
        state_by_protocol: Dict[str, Dict[str, int]] = {
            s.value: {} for s in DeliveryState
        }
        drop_reasons_by_protocol: Dict[str, Dict[str, int]] = {}
        first_event_ts: Optional[float] = None
        last_event_ts: Optional[float] = None
        # Cross-process health fields (set by the last writer's
        # preflight / write-error persist; see `_run_preflight` and
        # `_persist_write_error_state`). May be None when no writer
        # has touched this DB yet.
        db_preflight_ok: Optional[bool] = None
        db_preflight_ts: Optional[float] = None
        db_write_errors: Optional[int] = None
        db_write_error_ts: Optional[float] = None

        for key, value in rows:
            if key.startswith("state."):
                state_totals[key[6:]] = value
            elif key.startswith("drop."):
                drop_reasons[key[5:]] = value
            elif key.startswith("state_proto."):
                _, state_v, proto = key.split(".", 2)
                # Pre-F7 history carries lane labels; merge them into the
                # transport (+=, several keys can map to one protocol).
                by = state_by_protocol.setdefault(state_v, {})
                proto = canonical_protocol(proto)
                by[proto] = by.get(proto, 0) + value
            elif key.startswith("drop_proto."):
                _, reason_v, proto = key.split(".", 2)
                by = drop_reasons_by_protocol.setdefault(canonical_protocol(proto), {})
                by[reason_v] = by.get(reason_v, 0) + value
            elif key == "meta.first_event_ts":
                first_event_ts = value / 1000.0
            elif key == "meta.last_event_ts":
                last_event_ts = value / 1000.0
            elif key == "meta.preflight_ts":
                db_preflight_ts = value / 1000.0
            elif key == "meta.preflight_ok":
                db_preflight_ok = bool(value)
            elif key == "meta.consecutive_write_errors":
                db_write_errors = int(value)
            elif key == "meta.last_write_error_ts":
                db_write_error_ts = value / 1000.0

        # Honest confirmation accounting (MF #74 port, 2026-07-19): the old
        # cross-population `confirmed / sent` read 7.625 live on this fleet.
        confirmation = compute_confirmation_view(
            state_totals, state_by_protocol, drop_reasons,
            drop_reasons_by_protocol=drop_reasons_by_protocol)

        # events_rows comes back newest-first from the DESC ORDER BY;
        # the snapshot contract is newest-LAST so the operator can
        # tail a JSON dump and read the latest at the bottom.
        recent: List[Dict[str, Any]] = []
        for (ts, msg_id, state, protocol, drop_reason,
                source, note) in reversed(events_rows):
            d: Dict[str, Any] = {
                "ts": ts,
                "id": msg_id,
                "state": state,
                "protocol": canonical_protocol(protocol),
                "drop_reason": drop_reason,
            }
            if source:
                d["source"] = source
            if note:
                d["note"] = note
            recent.append(d)

        # Newest-LAST, same contract as `recent`. Compact by design: a consumer
        # judging confirmation rates needs state/protocol/drop_reason only.
        recent_terminal: List[Dict[str, Any]] = [
            {"ts": ts, "id": msg_id, "state": state,
             "protocol": canonical_protocol(protocol), "drop_reason": drop_reason}
            for (ts, msg_id, state, protocol, drop_reason)
            in reversed(terminal_rows)
        ]

        # Health block (Issue #63 / reliability backlog #2; #74
        # cross-process write-error truth). The cross-process truth is
        # what's persisted in the DB (`db_preflight_*` and
        # `db_write_errors*` — set by the last writer). Before #74 the
        # runtime write-error fields were local-only, so a READER
        # (the map daemon serving the watchdog probe) always saw 0 and
        # the probe's degraded branch could never fire. Now the count
        # merges via max(local, persisted): the reader (local 0) gets
        # the writer's persisted truth; the writer wins when its own
        # error-state persist also failed (DB fully down). The error
        # STRING stays local-only (counters values are numeric).
        # Operators interpret: `db_preflight_ok=false` → the last
        # writer failed at startup. `consecutive_write_errors>0` →
        # mid-run write regression (also: `last_event_ts` stale
        # relative to expected traffic).
        with self._lock:
            write_errors = max(
                self._consecutive_write_errors, db_write_errors or 0
            )
            # ts only meaningful alongside a nonzero count — a healed
            # write path reports no error state at all.
            write_error_ts = None if write_errors == 0 else max(
                (t for t in (self._last_write_error_ts, db_write_error_ts)
                 if t is not None),
                default=None,
            )
            health = {
                "db_path": str(self._db_path),
                "preflight_ok": (
                    db_preflight_ok if db_preflight_ok is not None
                    else self._preflight_ok
                ),
                "preflight_ts": (
                    db_preflight_ts if db_preflight_ts is not None
                    else self._preflight_ts
                ),
                "preflight_error": self._preflight_error,
                "last_successful_write_ts": last_event_ts,
                "consecutive_write_errors": write_errors,
                "last_write_error_ts": write_error_ts,
                "last_write_error": self._last_write_error,
            }
            if snapshot_failed:
                # The DB could not be read THIS call: every zero above is an
                # assertion, not an observation, and the preflight value is
                # the reader's stale startup state. Mark it so the Delivery
                # screen / probes read UNKNOWN instead of QUIET (MF parity,
                # delivery_counters.py `db_unobservable`; hfm #1, #2).
                health["db_unobservable"] = True
                health["preflight_ok"] = None

        (legs, legs_since_ts, legs_partial, legs_write_errors,
         legs_last_error_ts) = legs_view

        return {
            "state_totals": state_totals,
            "drop_reasons": drop_reasons,
            "state_by_protocol": state_by_protocol,
            **confirmation,
            "recent": recent,
            "recent_terminal": recent_terminal,
            "first_event_ts": first_event_ts,
            "last_event_ts": last_event_ts,
            "ring_capacity": ring_capacity,
            "legs": legs,
            "legs_since_ts": legs_since_ts,
            "legs_bucket_s": delivery_legs.BUCKET_S,   # readers derive reach
            "legs_partial_windows": legs_partial,
            "legs_write_errors": legs_write_errors,
            "legs_last_write_error_ts": legs_last_error_ts,
            "health": health,
        }

    def recent(self, limit: Optional[int] = None) -> List[DeliveryEvent]:
        """Return recent events (newest last). ``limit`` clamps the
        returned list; ``None`` returns everything in the events
        table."""
        try:
            with self._connect() as conn:
                if limit is None:
                    cursor = conn.execute(
                        "SELECT ts, id, state, protocol, drop_reason, note, "
                        "source "
                        "FROM events ORDER BY rowid ASC"
                    )
                elif limit <= 0:
                    return []
                else:
                    cursor = conn.execute(
                        "SELECT ts, id, state, protocol, drop_reason, note, "
                        "source "
                        "FROM events ORDER BY rowid DESC LIMIT ?",
                        (limit,),
                    )
                rows = cursor.fetchall()
        except sqlite3.Error:
            return []
        if limit is not None and limit > 0:
            rows = list(reversed(rows))
        return [self._row_to_event(r) for r in rows]

    def history_for(self, msg_id: str) -> List[DeliveryEvent]:
        """All retained events for one message id, oldest first.

        Bounded by the ring cap — older events may have aged out. The
        durable trace lives in
        ``PersistentMessageQueue.message_lifecycle``; this is the
        fast in-memory-ish lookup."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT ts, id, state, protocol, drop_reason, note, source "
                    "FROM events WHERE id = ? ORDER BY rowid ASC",
                    (str(msg_id),),
                ).fetchall()
        except sqlite3.Error:
            return []
        return [self._row_to_event(r) for r in rows]

    @staticmethod
    def _row_to_event(
        row: Tuple[float, str, str, Optional[str], Optional[str], str, str],
    ) -> DeliveryEvent:
        ts, msg_id, state, protocol, drop_reason, note, source = row
        try:
            state_enum = DeliveryState(state)
        except ValueError:
            state_enum = DeliveryState.DROPPED
        dr_enum: Optional[DropReason] = None
        if drop_reason:
            try:
                dr_enum = DropReason(drop_reason)
            except ValueError:
                dr_enum = DropReason.UNKNOWN
        return DeliveryEvent(
            ts=ts, id=msg_id, state=state_enum,
            protocol=canonical_protocol(protocol), drop_reason=dr_enum,
            note=note or "", source=source or None,
        )

    # ── test helpers ───────────────────────────────────────────

    def _reset_for_tests(self) -> None:
        """Drop all rows. Production code must never call."""
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM counters")
                conn.execute("DELETE FROM events")
                conn.execute("DELETE FROM leg_buckets")
                conn.execute("DELETE FROM leg_meta")
                conn.commit()
        except sqlite3.Error:
            pass


# ── Process-wide singleton ───────────────────────────────────────────


_singleton: Optional[DeliveryCounters] = None
_singleton_lock = threading.Lock()


def get_singleton() -> DeliveryCounters:
    """Return the process-wide counter handle.

    All call sites in one process share this handle; multiple
    processes (gateway daemon + map daemon) construct independent
    handles that point at the same SQLite DB."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = DeliveryCounters()
        return _singleton


def record(
    state: DeliveryState,
    msg_id: str,
    *,
    protocol: Optional[str] = None,
    drop_reason: Optional[DropReason] = None,
    note: str = "",
    source: Optional[str] = None,
) -> DeliveryEvent:
    """Module-level convenience — bumps the singleton's counters."""
    return get_singleton().record(
        state, msg_id,
        protocol=protocol, drop_reason=drop_reason, note=note, source=source,
    )


def snapshot(recent_limit: int = SNAPSHOT_RECENT_LIMIT) -> Dict[str, Any]:
    """Module-level convenience for the HTTP handler."""
    return get_singleton().snapshot(recent_limit=recent_limit)


def _reset_singleton_for_tests() -> None:
    """Test-only: drop the singleton. Production code must never call."""
    global _singleton
    with _singleton_lock:
        _singleton = None
