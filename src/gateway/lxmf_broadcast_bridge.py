"""LXMF Broadcast Bridge — fan out MeshCore channels as LXMF DMs.

A focused plug-in on top of the existing RNS runtime. The bridge runs
its OWN `LXMF.LXMRouter` (sharing the process-wide `RNS.Transport`) so
it can register a delivery identity distinct from the gateway's. LXMF
0.9.4's `register_delivery_identity` returns None if one is already
registered on the router — so a single shared router can't host both
the gateway's identity and the broadcast identity.

Subscription protocol (over LXMF DM to the broadcast identity):

    "subscribe"      — add sender to fan-out list
    "unsubscribe"    — remove from fan-out list
    "channels"       — reply with the channel allowlist
    "help"           — short usage reply

Message flow:

    MeshCore channel RX
        → CanonicalMessage (source_network="meshcore", is_broadcast=True)
        → channel allowlist filter
        → format prefix
        → for each subscriber: LXMF.LXMessage(broadcast_identity → sub)
        → own_router.handle_outbound

This is intentionally RX-only fan-out. No reverse path back into
MeshCore is implemented yet — keeping loops trivially impossible until
the propagation rules are designed.

Persistence:
    ~/.config/meshanchor/lxmf_broadcast_identity   (RNS.Identity)
    ~/.config/meshanchor/lxmf_broadcast_storage/   (LXMF router state)
    ~/.config/meshanchor/lxmf_broadcast_subs.db    (sqlite, tracked in
                                                    utils.db_inventory)
"""

from __future__ import annotations

import json
import logging
import signal as _signal_mod
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils.boundary_timing import call_boundary
from utils.db_helpers import connect_tuned
from utils.lxmf_broadcast_metrics import (
    record_fanout,
    record_state_transition,
    register_status_provider,
    unregister_status_provider,
)
from utils.paths import get_real_user_home
from utils.safe_import import safe_import

from .canonical_message import CanonicalMessage, MessageType, Protocol
from .config import GatewayConfig, LXMFBroadcastConfig

_RNS_mod, _HAS_RNS = safe_import("RNS")
_LXMF_mod, _HAS_LXMF = safe_import("LXMF")

logger = logging.getLogger(__name__)


@contextmanager
def _suppress_signal_in_thread():
    """Disable signal.signal() registration on non-main threads.

    LXMF.LXMRouter() and RNS.Reticulum() register SIGINT/SIGTERM handlers
    in their constructors. Python only allows signal handler registration
    from the main thread, so the bridge — which starts from
    RNSMeshtasticBridge._rns_loop running in a background thread —
    blows up at LXMRouter() with "signal only works in main thread".

    Same fix the gateway uses (RNSConnectionMixin._suppress_signal_in_thread):
    monkey-patch signal.signal to a no-op for the duration of the boot,
    then restore.
    """
    if threading.current_thread() is threading.main_thread():
        yield
        return
    original = _signal_mod.signal

    def _safe_signal(signalnum, handler):
        return _signal_mod.SIG_DFL

    _signal_mod.signal = _safe_signal
    try:
        yield
    finally:
        _signal_mod.signal = original


# Subscription protocol verbs are case-insensitive and matched on the
# first word of the message body so operators can DM "subscribe please"
# from a phone keyboard without exact syntax pain.
_VERB_SUBSCRIBE = "subscribe"
_VERB_UNSUBSCRIBE = "unsubscribe"
_VERB_CHANNELS = "channels"
_VERB_HELP = "help"


# Tier categorises where a subscriber came from: local clients on this pi
# (NomadNet, MeshChatX), federation peers (other NOCs), or external random
# RNS peers that DMed `subscribe`. Drives tier-aware retry budgets (S3) and
# alertability (S4).
TIER_LOCAL = "local"
TIER_FEDERATION = "federation"
TIER_EXTERNAL = "external"

# State machine values. mark_delivered always returns to `healthy`.
# Automatic degraded/stale/dead transitions land in mark_failed.
STATE_HEALTHY = "healthy"
STATE_DEGRADED = "degraded"
STATE_STALE = "stale"
STATE_DEAD = "dead"

# Per-tier backoff windows: (floor_seconds, cap_seconds). Backoff after N
# failures = min(floor * 2^(N-1), cap). Local clients on this pi recover
# fast (small floor); external random peers get lazy retries so a dead
# stranger doesn't burn cycles every broadcast.
_BACKOFF_BY_TIER = {
    TIER_LOCAL:      (1.0,  600.0),    # 1s → 10m cap (~10 fails to reach cap)
    TIER_FEDERATION: (5.0,  1800.0),   # 5s → 30m cap
    TIER_EXTERNAL:   (30.0, 3600.0),   # 30s → 1h cap
}

# State-transition thresholds.
_DEGRADED_FAIL_THRESHOLD = 3                  # consecutive failures
_STALE_SINCE_SUCCESS_SEC = 24 * 3600          # 24h with no success
_DEAD_SINCE_SUCCESS_SEC = 7 * 24 * 3600       # 7d with no success


@dataclass
class Subscriber:
    """One LXMF identity opted in to the broadcast fan-out."""
    lxmf_hash: str          # 16-char hex destination hash
    added_at: datetime
    last_delivery: Optional[datetime] = None
    tier: str = TIER_EXTERNAL
    state: str = STATE_HEALTHY
    consecutive_failures: int = 0
    last_failure_at: Optional[datetime] = None


class SubscriberStore:
    """SQLite-backed subscriber list. Single table, no auto-prune.

    Operators add/remove via the LXMF subscription protocol; we never
    GC entries automatically because losing a subscriber silently is
    worse than carrying a dead one (the LXMF router will simply fail
    to find a path).
    """

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        """Create table + apply S1 column migrations.

        Live `meshanchor-server` already has a populated subscribers table
        from before the state-machine columns existed; `ADD COLUMN ... DEFAULT`
        is the idempotent path that promotes pre-S1 rows to
        `tier='external'`, `state='healthy'`, `consecutive_failures=0` without
        copying data. SQLite tolerates the column already existing only if we
        guard with a `PRAGMA table_info` check, which is what this does.
        """
        with self._lock, connect_tuned(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subscribers (
                    lxmf_hash TEXT PRIMARY KEY,
                    added_at TEXT NOT NULL,
                    last_delivery TEXT
                )
                """
            )
            existing_cols = {
                row[1]  # row = (cid, name, type, notnull, dflt_value, pk)
                for row in conn.execute("PRAGMA table_info(subscribers)")
            }
            migrations = [
                ("tier", f"TEXT NOT NULL DEFAULT '{TIER_EXTERNAL}'"),
                ("state", f"TEXT NOT NULL DEFAULT '{STATE_HEALTHY}'"),
                ("consecutive_failures", "INTEGER NOT NULL DEFAULT 0"),
                ("last_failure_at", "TEXT"),
            ]
            for col_name, col_def in migrations:
                if col_name in existing_cols:
                    continue
                conn.execute(
                    f"ALTER TABLE subscribers ADD COLUMN {col_name} {col_def}"
                )
            conn.commit()

    def add(self, lxmf_hash: str, tier: str = TIER_EXTERNAL) -> bool:
        """Add subscriber. Returns True if newly added, False if already present.

        `tier` is set at first insert and not touched by subsequent `add()`
        calls — the idempotency contract still holds. S2 promotes existing
        rows in-place via a separate path when auto-discovery lights up.
        """
        lxmf_hash = lxmf_hash.lower()
        now = datetime.utcnow().isoformat()
        with self._lock, connect_tuned(self._db_path) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO subscribers("
                "lxmf_hash, added_at, tier, state, consecutive_failures"
                ") VALUES (?, ?, ?, ?, 0)",
                (lxmf_hash, now, tier, STATE_HEALTHY),
            )
            conn.commit()
            return cur.rowcount > 0

    def remove(self, lxmf_hash: str) -> bool:
        """Remove subscriber. Returns True if it was present."""
        lxmf_hash = lxmf_hash.lower()
        with self._lock, connect_tuned(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM subscribers WHERE lxmf_hash = ?", (lxmf_hash,)
            )
            conn.commit()
            return cur.rowcount > 0

    _ALL_COLS = (
        "lxmf_hash, added_at, last_delivery, tier, state, "
        "consecutive_failures, last_failure_at"
    )

    def _row_to_subscriber(self, row) -> Subscriber:
        h, added, last, tier, state, fails, failed = row
        return Subscriber(
            lxmf_hash=h,
            added_at=_parse_iso(added) or datetime.utcnow(),
            last_delivery=_parse_iso(last),
            tier=tier or TIER_EXTERNAL,
            state=state or STATE_HEALTHY,
            consecutive_failures=int(fails or 0),
            last_failure_at=_parse_iso(failed),
        )

    def list_all(self) -> List[Subscriber]:
        with self._lock, connect_tuned(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT {self._ALL_COLS} FROM subscribers"
            ).fetchall()
        return [self._row_to_subscriber(r) for r in rows]

    def list_by_state(self, state: str) -> List[Subscriber]:
        with self._lock, connect_tuned(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT {self._ALL_COLS} FROM subscribers WHERE state = ?",
                (state,),
            ).fetchall()
        return [self._row_to_subscriber(r) for r in rows]

    def list_by_tier(self, tier: str) -> List[Subscriber]:
        with self._lock, connect_tuned(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT {self._ALL_COLS} FROM subscribers WHERE tier = ?",
                (tier,),
            ).fetchall()
        return [self._row_to_subscriber(r) for r in rows]

    def mark_delivered(self, lxmf_hash: str) -> None:
        """Record a successful fan-out: timestamp + reset failure counter.

        Also drops the row back to `healthy` from any prior degraded state —
        a successful delivery is the strongest possible health signal. S3's
        automatic state transitions still respect this reset.
        """
        lxmf_hash = lxmf_hash.lower()
        now = datetime.utcnow().isoformat()
        prior_state: Optional[str] = None
        with self._lock, connect_tuned(self._db_path) as conn:
            row = conn.execute(
                "SELECT state FROM subscribers WHERE lxmf_hash = ?",
                (lxmf_hash,),
            ).fetchone()
            if row:
                prior_state = row[0]
            conn.execute(
                "UPDATE subscribers "
                "SET last_delivery = ?, consecutive_failures = 0, state = ? "
                "WHERE lxmf_hash = ?",
                (now, STATE_HEALTHY, lxmf_hash),
            )
            conn.commit()
        # Recovery transition: only emit when state actually changed.
        if prior_state and prior_state != STATE_HEALTHY:
            record_state_transition(prior_state, STATE_HEALTHY)
            logger.info(json.dumps({
                "event": "lxmf_broadcast_state_transition",
                "hash": lxmf_hash[:8],
                "from": prior_state,
                "to": STATE_HEALTHY,
                "fails": 0,
                "reason": "delivery_succeeded",
            }))

    @staticmethod
    def desired_state(sub: "Subscriber", now: datetime) -> str:
        """Pure function: target state for a subscriber based on row data.

        Three rules, evaluated in order from "worst" outward so the most
        severe degradation wins:
          1. 7+ days without a success while currently failing → dead.
          2. 24+ hours without a success while currently failing → stale.
          3. 3+ consecutive failures (recent) → degraded.
          4. otherwise → healthy.

        `mark_delivered` resets failures to 0 and state to healthy directly,
        so this function only needs to handle the failure side.
        """
        if sub.consecutive_failures == 0:
            return STATE_HEALTHY
        last_success = sub.last_delivery or sub.added_at
        if last_success is None:
            # No reference point — fall back to "since failures started".
            last_success = sub.last_failure_at or now
        since_success = (now - last_success).total_seconds()
        if since_success >= _DEAD_SINCE_SUCCESS_SEC:
            return STATE_DEAD
        if since_success >= _STALE_SINCE_SUCCESS_SEC:
            return STATE_STALE
        if sub.consecutive_failures >= _DEGRADED_FAIL_THRESHOLD:
            return STATE_DEGRADED
        return STATE_HEALTHY

    @staticmethod
    def backoff_seconds(sub: "Subscriber") -> float:
        """Seconds to wait between attempts for this subscriber. 0 = no wait."""
        if sub.consecutive_failures == 0:
            return 0.0
        floor, cap = _BACKOFF_BY_TIER.get(
            sub.tier, _BACKOFF_BY_TIER[TIER_EXTERNAL]
        )
        # 2**(N-1) so fails=1 → floor (not floor*2)
        return min(floor * (2 ** (sub.consecutive_failures - 1)), cap)

    def should_attempt(
        self, sub: "Subscriber", now: Optional[datetime] = None,
    ) -> bool:
        """Return True if the next broadcast should be attempted for `sub`.

        Healthy subscribers always pass. Failing subscribers must wait their
        tier's backoff window since the last failure; in-window attempts are
        skipped and counted as `stats["skipped_backoff"]` by the bridge.
        """
        if sub.consecutive_failures == 0:
            return True
        if sub.last_failure_at is None:
            return True
        window = self.backoff_seconds(sub)
        elapsed = ((now or datetime.utcnow()) - sub.last_failure_at).total_seconds()
        return elapsed >= window

    def mark_failed(self, lxmf_hash: str, reason: str) -> None:
        """Record a failed fan-out: timestamp + counter + auto-state.

        After the counter increments, this method calls `desired_state` to
        decide whether to transition healthy → degraded → stale → dead.
        State transitions log at INFO; the per-failure debug log stays.
        """
        lxmf_hash = lxmf_hash.lower()
        now = datetime.utcnow()
        now_iso = now.isoformat()
        prior_state: Optional[str] = None
        new_state: Optional[str] = None
        new_fails: int = 0
        with self._lock, connect_tuned(self._db_path) as conn:
            cur = conn.execute(
                "UPDATE subscribers "
                "SET last_failure_at = ?, "
                "    consecutive_failures = consecutive_failures + 1 "
                "WHERE lxmf_hash = ?",
                (now_iso, lxmf_hash),
            )
            if cur.rowcount == 0:
                # Ephemeral subscriber (e.g. _reply path) — nothing to track.
                conn.commit()
                return
            row = conn.execute(
                f"SELECT {self._ALL_COLS} FROM subscribers "
                f"WHERE lxmf_hash = ?",
                (lxmf_hash,),
            ).fetchone()
            sub = self._row_to_subscriber(row)
            new_fails = sub.consecutive_failures
            target = self.desired_state(sub, now)
            if target != sub.state:
                conn.execute(
                    "UPDATE subscribers SET state = ? WHERE lxmf_hash = ?",
                    (target, lxmf_hash),
                )
                prior_state = sub.state
                new_state = target
            conn.commit()
        logger.debug(
            "LXMF subscriber %s fan-out failed: %s", lxmf_hash[:8], reason
        )
        if new_state and prior_state:
            record_state_transition(prior_state, new_state)
            # Structured event for log scrapers — short hash for privacy.
            logger.info(json.dumps({
                "event": "lxmf_broadcast_state_transition",
                "hash": lxmf_hash[:8],
                "from": prior_state,
                "to": new_state,
                "fails": new_fails,
                "reason": reason,
            }))

    def transition_state(self, lxmf_hash: str, new_state: str) -> bool:
        """Explicit state setter for S3+. Returns True if a row was updated.

        Logs at INFO so state changes leave an audit trail without callers
        needing to remember to log.
        """
        lxmf_hash = lxmf_hash.lower()
        with self._lock, connect_tuned(self._db_path) as conn:
            cur = conn.execute(
                "UPDATE subscribers SET state = ? WHERE lxmf_hash = ?",
                (new_state, lxmf_hash),
            )
            conn.commit()
            updated = cur.rowcount > 0
        if updated:
            logger.info(
                "LXMF subscriber %s → state=%s", lxmf_hash[:8], new_state
            )
        return updated


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def format_broadcast_text(
    msg: CanonicalMessage, prefix_format: str
) -> str:
    """Render a CanonicalMessage as the LXMF body text.

    Tries to honor the operator's prefix_format with sensible defaults
    when fields are missing; never raises on bad templates so a typo
    in the config doesn't take the bridge down.
    """
    channel = msg.metadata.get("channel", 0) if msg.metadata else 0
    sender = msg.source_address or "?"
    # Trim long sender keys (MeshCore pubkeys are 12 hex chars; that's
    # already short enough, but Meshtastic node IDs can be 9 chars
    # including the "!" prefix — leave them alone)
    if len(sender) > 16:
        sender = sender[:16]
    text = msg.content or ""
    try:
        return prefix_format.format(channel=channel, sender=sender, text=text)
    except (KeyError, IndexError, ValueError) as e:
        logger.warning("Bad prefix_format %r: %s — using fallback", prefix_format, e)
        return f"[ch{channel}:{sender}] {text}"


class LXMFBroadcastBridge:
    """MeshCore→LXMF fan-out bridge.

    Owns its own LXMRouter so it can register a distinct delivery
    identity — LXMF 0.9.4 caps the gateway router at one identity.

    Lifecycle:
        bridge = LXMFBroadcastBridge(config)
        bridge.start()           # boots own LXMRouter, announces
        ...
        bridge.on_meshcore_message(canonical_msg)   # called from gateway
        ...
        bridge.stop()
    """

    def __init__(
        self,
        broadcast_config: LXMFBroadcastConfig,
        rns_module: Any = None,
        lxmf_module: Any = None,
        identity_path: Optional[Path] = None,
        db_path: Optional[Path] = None,
        storage_path: Optional[Path] = None,
        propagation_node: str = "",
        persistent_queue: Any = None,
        ack_emit_callback: Optional[Callable[[str, str], bool]] = None,
    ) -> None:
        self._config = broadcast_config
        self._rns = rns_module if rns_module is not None else _RNS_mod
        self._lxmf = lxmf_module if lxmf_module is not None else _LXMF_mod
        self._propagation_node = (propagation_node or "").strip()
        # Issue #66 first-caller wiring. The parent RNSMeshtasticBridge
        # owns the PersistentMessageQueue (where pending-ack records live)
        # and the _maybe_emit_ack_for_msgid callable (which turns a
        # winning LXMF delivery into a synthetic [delivered:<id>] back to
        # the originating MeshCore device). Both are optional — when None,
        # the bridge falls back to its pre-Issue-#66 behavior of pure
        # fan-out with no ack tracking. Only used when the operator sets
        # broadcast_config.ack_required = True.
        self._persistent_queue = persistent_queue
        self._ack_emit_callback = ack_emit_callback

        config_dir = get_real_user_home() / ".config" / "meshanchor"
        self._identity_path = identity_path or (
            Path(broadcast_config.identity_file)
            if broadcast_config.identity_file
            else config_dir / "lxmf_broadcast_identity"
        )
        db = db_path or (
            Path(broadcast_config.db_file)
            if broadcast_config.db_file
            else config_dir / "lxmf_broadcast_subs.db"
        )
        self._storage_path = storage_path or (config_dir / "lxmf_broadcast_storage")
        self._subs = SubscriberStore(db)

        # Filled in start()
        self._identity = None
        self._router = None
        self._lxmf_source = None
        self._destination_hash: Optional[bytes] = None

        self._running = False
        self._started_at: Optional[datetime] = None
        self._stop_event = threading.Event()
        self._announce_thread: Optional[threading.Thread] = None

        self._stats_lock = threading.Lock()
        self.stats: Dict[str, int] = {
            "fanouts": 0,           # total LXMF DMs sent
            "subscribes": 0,
            "unsubscribes": 0,
            "filtered_channel": 0,  # MeshCore msgs dropped by channel filter
            "filtered_non_meshcore": 0,
            "errors": 0,
            "skipped_backoff": 0,   # fan-outs skipped because subscriber in
                                    # backoff cooldown after consecutive fails
        }

    # ------------------------------------------------------------------
    # Identity / lifecycle
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def destination_hash_hex(self) -> str:
        return self._destination_hash.hex() if self._destination_hash else ""

    def start(self) -> bool:
        """Boot own LXMRouter, register identity, start announce thread."""
        if self._running:
            return True
        if self._rns is None or self._lxmf is None:
            logger.error("LXMF broadcast bridge: RNS/LXMF not installed")
            return False

        try:
            self._storage_path.mkdir(parents=True, exist_ok=True)
            self._identity = self._load_or_create_identity()

            # Own LXMRouter — separate from gateway's so register_delivery_identity
            # doesn't trip LXMF 0.9.4's "one identity per router" cap.
            # LXMRouter() registers signal handlers internally and fails on
            # non-main threads; the bridge runs on _rns_thread, so we
            # suppress signal registration during construction.
            with _suppress_signal_in_thread():
                self._router = self._lxmf.LXMRouter(storagepath=str(self._storage_path))
            self._router.register_delivery_callback(self._on_lxmf_delivery)

            self._lxmf_source = self._router.register_delivery_identity(
                self._identity,
                display_name=self._config.display_name,
            )
            if self._lxmf_source is None:
                logger.error(
                    "LXMF broadcast bridge: register_delivery_identity returned None "
                    "(router state corrupt — wipe %s and restart)",
                    self._storage_path,
                )
                return False

            self._destination_hash = getattr(self._lxmf_source, "hash", None)
            if self._destination_hash is None:
                logger.error("LXMF broadcast bridge: source has no .hash attribute")
                return False

            logger.info(
                "LXMF broadcast identity registered: %s (%s)",
                self._config.display_name,
                self._destination_hash.hex(),
            )

            # Optional: copy gateway's propagation node so subscribers offline
            # still pick up fan-outs via store-and-forward.
            if self._propagation_node:
                try:
                    self._router.set_outbound_propagation_node(
                        bytes.fromhex(self._propagation_node)
                    )
                    logger.info(
                        "LXMF broadcast propagation node: %s", self._propagation_node
                    )
                except (ValueError, TypeError) as e:
                    logger.warning(
                        "Invalid propagation_node hash %r: %s",
                        self._propagation_node,
                        e,
                    )

            # First announce now; thread handles re-announce cadence.
            self._safe_announce()

            self._running = True
            self._started_at = datetime.utcnow()
            self._stop_event.clear()
            if self._config.announce_interval_sec > 0:
                self._announce_thread = threading.Thread(
                    target=self._announce_loop,
                    name="LXMFBroadcast-Announce",
                    daemon=True,
                )
                self._announce_thread.start()
            _set_active_bridge(self)
            register_status_provider(self.get_status)
            return True
        except Exception as e:
            logger.error("LXMF broadcast bridge failed to start: %s", e)
            with self._stats_lock:
                self.stats["errors"] += 1
            return False

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._announce_thread and self._announce_thread.is_alive():
            self._announce_thread.join(timeout=3)
        self._announce_thread = None
        # LXMRouter has no clean shutdown method in 0.9.4; we drop the
        # reference and let GC clean up. Re-creating on next start is
        # safe because storagepath persistence handles state.
        self._router = None
        self._lxmf_source = None
        self._destination_hash = None
        self._started_at = None
        _clear_active_bridge(self)
        unregister_status_provider()

    def _load_or_create_identity(self):
        path = self._identity_path
        path.parent.mkdir(parents=True, exist_ok=True)
        RNS = self._rns
        if path.exists():
            try:
                return RNS.Identity.from_file(str(path))
            except Exception as e:
                logger.warning("Could not load broadcast identity %s: %s — recreating", path, e)
        identity = RNS.Identity()
        identity.to_file(str(path))
        return identity

    def _announce_loop(self) -> None:
        # Loop guards against announce_interval_sec being changed mid-run
        # by always re-reading the current value.
        while self._running and not self._stop_event.is_set():
            interval = max(60, int(self._config.announce_interval_sec))
            if self._stop_event.wait(interval):
                return
            self._safe_announce()

    def _safe_announce(self) -> None:
        if self._destination_hash is None:
            return
        try:
            self._router.announce(self._destination_hash)
            logger.debug("LXMF broadcast announce sent (%s)", self._destination_hash.hex())
        except Exception as e:
            logger.debug("Announce failed (will retry): %s", e)

    # ------------------------------------------------------------------
    # Inbound — from gateway hooks
    # ------------------------------------------------------------------

    def on_meshcore_message(self, msg: CanonicalMessage) -> None:
        """Gateway message-callback hook. Filters and fans out."""
        if not self._running:
            return
        # Issue #66 first-caller DIAGNOSTIC (temporary): log every entry
        # with the guard-decision fields so we can see why ack-registration
        # isn't firing in production. Roll back once the cause is confirmed.
        logger.info(
            "ISSUE66_DIAG on_meshcore_message entry: "
            "src_net=%r src_addr=%r is_bcast=%s msg_type=%s "
            "channel=%s content_head=%r "
            "ack_required=%s have_pq=%s have_emit=%s "
            "synth_marker=%s",
            msg.source_network,
            msg.source_address,
            getattr(msg, 'is_broadcast', None),
            getattr(msg, 'message_type', None),
            (msg.metadata or {}).get('channel'),
            (msg.content or '')[:40],
            self._config.ack_required,
            self._persistent_queue is not None,
            self._ack_emit_callback is not None,
            (msg.metadata or {}).get('meshforge_is_synth_ack'),
        )
        if msg.source_network != Protocol.MESHCORE.value:
            with self._stats_lock:
                self.stats["filtered_non_meshcore"] += 1
            return
        if not msg.is_broadcast:
            # Direct messages handled by the (future) cross-mesh DM path.
            return
        if msg.message_type not in (MessageType.TEXT, MessageType.TACTICAL):
            return
        if not msg.content:
            return

        channel = (msg.metadata or {}).get("channel", 0)
        if self._config.channels and channel not in self._config.channels:
            with self._stats_lock:
                self.stats["filtered_channel"] += 1
            return

        body = format_broadcast_text(msg, self._config.prefix_format)
        title = "MeshCore"

        subscribers = self._subs.list_all()
        if not subscribers:
            return

        # Issue #66 first-caller: mint ONE broadcast msg_id and register
        # ONE pending-ack record per fan-out (not per subscriber). The
        # first subscriber whose LXMF delivery callback fires wins —
        # PersistentMessageQueue.mark_acked is idempotent (returns None
        # on 2nd call) so only the first emits the synthetic ACK back to
        # the originating MeshCore device. Recursion-guard: never track
        # a message that's already tagged as a synth ACK.
        ack_msg_id: Optional[str] = None
        meta = msg.metadata or {}
        if (self._config.ack_required
                and self._persistent_queue is not None
                and self._ack_emit_callback is not None
                and msg.source_address
                and not meta.get("meshforge_is_synth_ack")):
            ack_msg_id = f"bcast-{int(time.time() * 1000)}-ch{channel}"
            try:
                ok = self._persistent_queue.register_pending_ack(
                    ack_msg_id,
                    origin_network="meshcore",
                    origin_address=msg.source_address,
                    timeout_seconds=300,
                    allow_orphan=True,
                )
                if not ok:
                    logger.error(
                        "register_pending_ack returned False for %s "
                        "(allow_orphan was True — this is a hard DB error, "
                        "not a missing-row condition)",
                        ack_msg_id[:16],
                    )
                    ack_msg_id = None
            except Exception as e:
                # Don't let an ack-registration failure block fan-out.
                logger.warning(
                    "register_pending_ack failed for %s: %s",
                    ack_msg_id[:16], e,
                )
                ack_msg_id = None

        now = datetime.utcnow()
        skipped = 0
        for sub in subscribers:
            if not self._subs.should_attempt(sub, now=now):
                skipped += 1
                continue
            self._send_to_subscriber(sub, body, title, ack_msg_id=ack_msg_id)
        if skipped:
            with self._stats_lock:
                self.stats["skipped_backoff"] += skipped

    def _on_lxmf_delivery(self, lxmf_message: Any) -> None:
        """LXMRouter delivery callback.

        Our private router only delivers messages addressed to our own
        identity, so every call here is a subscription command.
        """
        if not self._running:
            return
        try:
            source_hash = getattr(lxmf_message, "source_hash", b"")
            source_hex = source_hash.hex() if isinstance(source_hash, bytes) else str(source_hash)
            content_raw = getattr(lxmf_message, "content", b"") or b""
            if isinstance(content_raw, bytes):
                content = content_raw.decode("utf-8", errors="replace")
            else:
                content = str(content_raw)

            self._handle_subscription_command(source_hex, content)
        except Exception as e:
            logger.error("LXMF broadcast inbound error: %s", e)
            with self._stats_lock:
                self.stats["errors"] += 1

    def _handle_subscription_command(self, source_hex: str, body: str) -> None:
        verb = (body.strip().split() or [""])[0].lower()

        if verb == _VERB_SUBSCRIBE:
            added = self._subs.add(source_hex)
            with self._stats_lock:
                self.stats["subscribes"] += 1
            reply = (
                "Subscribed. You'll receive MeshCore channel "
                f"{self._config.channels} as LXMF DMs. Send 'unsubscribe' to stop."
                if added
                else "You are already subscribed."
            )
            self._reply(source_hex, reply)
        elif verb == _VERB_UNSUBSCRIBE:
            removed = self._subs.remove(source_hex)
            with self._stats_lock:
                self.stats["unsubscribes"] += 1
            reply = "Unsubscribed." if removed else "You were not subscribed."
            self._reply(source_hex, reply)
        elif verb == _VERB_CHANNELS:
            self._reply(
                source_hex,
                f"Bridging MeshCore channels: {self._config.channels}",
            )
        elif verb == _VERB_HELP or verb == "":
            self._reply(
                source_hex,
                "MeshAnchor LXMF broadcast bridge. Commands: subscribe, "
                "unsubscribe, channels, help.",
            )
        else:
            # Unknown verb: only auto-handle if autosubscribe is on,
            # otherwise stay quiet so we don't echo random LXMF traffic.
            if self._config.autosubscribe:
                self._subs.add(source_hex)
                with self._stats_lock:
                    self.stats["subscribes"] += 1
                self._reply(source_hex, "Subscribed (auto).")

    # ------------------------------------------------------------------
    # Outbound LXMF
    # ------------------------------------------------------------------

    def _send_to_subscriber(self, sub: Subscriber, body: str, title: str,
                            *, ack_msg_id: Optional[str] = None) -> bool:
        """Fan a broadcast out to one subscriber.

        ack_msg_id, when set, is the shared broadcast-level msg_id passed
        from on_meshcore_message. We register on_delivered/on_failed on
        the LXMessage; the first subscriber whose receipt confirms causes
        _ack_emit_callback(msg_id, 'delivered') and the substrate emits
        the synthetic ACK back to the MeshCore origin. Subsequent winners
        are idempotent — mark_acked returns None on the 2nd call. We do
        NOT call the emit callback with kind='failed' per subscriber;
        per-subscriber failures don't mean overall failure, and the
        substrate's timeout sweep handles "no subscriber acked" cleanly.
        """
        if self._router is None or self._lxmf_source is None:
            return False
        RNS = self._rns
        LXMF = self._lxmf
        tier = sub.tier
        try:
            dest_hash = bytes.fromhex(sub.lxmf_hash)
        except ValueError:
            logger.warning("Invalid subscriber hash %r — skipping", sub.lxmf_hash)
            self._subs.mark_failed(sub.lxmf_hash, "invalid_hash")
            record_fanout(tier, "invalid_hash")
            return False

        hash_short = sub.lxmf_hash[:8]
        start_t = time.monotonic()
        try:
            if not call_boundary("rnsd.has_path",
                                 RNS.Transport.has_path, dest_hash,
                                 target=hash_short):
                call_boundary("rnsd.request_path",
                              RNS.Transport.request_path, dest_hash,
                              target=hash_short)
                # Brief wait for path; do NOT block the calling thread
                # for long — the LXMF router will retry via propagation
                # if we set one in RNSConfig.
                for _ in range(30):
                    if RNS.Transport.has_path(dest_hash):
                        break
                    if self._stop_event.wait(0.1):
                        record_fanout(tier, "aborted",
                                      duration_s=time.monotonic() - start_t)
                        return False

            dest_identity = call_boundary(
                "rnsd.identity_recall",
                RNS.Identity.recall, dest_hash,
                target=hash_short,
            )
            if dest_identity is None:
                logger.debug("No identity recalled for %s — dropping fanout", sub.lxmf_hash)
                self._subs.mark_failed(sub.lxmf_hash, "identity_recall_null")
                record_fanout(tier, "identity_recall_null",
                              duration_s=time.monotonic() - start_t)
                return False

            destination = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery",
            )
            lxm = LXMF.LXMessage(destination, self._lxmf_source, body, title)

            # Issue #66 first-wins: wire LXMF delivery callbacks ONLY when
            # the operator opted in (ack_msg_id is set) and the substrate
            # is reachable. Captured-by-closure ack_msg_id ensures every
            # subscriber's callback addresses the same pending-ack record.
            if ack_msg_id and self._ack_emit_callback is not None:
                msg_id_capture = ack_msg_id
                emit = self._ack_emit_callback

                def on_delivered(receipt):
                    try:
                        emit(msg_id_capture, "delivered")
                    except Exception as e:
                        logger.debug(
                            "ack emit (delivered) failed for %s: %s",
                            msg_id_capture[:16], e,
                        )

                def on_failed(receipt):
                    # Per-subscriber failure is NOT overall failure — the
                    # substrate's timeout sweep handles "no winner" via
                    # synthetic [timeout:<id>]. We just log here.
                    logger.debug(
                        "LXMF delivery failed to %s for ack %s",
                        hash_short, msg_id_capture[:16],
                    )

                try:
                    lxm.register_delivery_callback(on_delivered)
                    lxm.register_failed_callback(on_failed)
                except (AttributeError, TypeError):
                    logger.debug(
                        "LXMF callbacks not available — fan-out to %s "
                        "will not contribute to ack synthesis",
                        hash_short,
                    )

            call_boundary("rnsd.handle_outbound",
                          self._router.handle_outbound, lxm,
                          target=hash_short)
            self._subs.mark_delivered(sub.lxmf_hash)
            with self._stats_lock:
                self.stats["fanouts"] += 1
            record_fanout(tier, "success",
                          duration_s=time.monotonic() - start_t)
            return True
        except Exception as e:
            logger.debug("Fanout to %s failed: %s", sub.lxmf_hash, e)
            self._subs.mark_failed(sub.lxmf_hash, f"outbound_error:{type(e).__name__}")
            with self._stats_lock:
                self.stats["errors"] += 1
            record_fanout(tier, "outbound_error",
                          duration_s=time.monotonic() - start_t)
            return False

    def _reply(self, source_hex: str, body: str) -> bool:
        """Send a one-shot reply to a subscription-protocol command."""
        sub = Subscriber(lxmf_hash=source_hex, added_at=datetime.utcnow())
        return self._send_to_subscriber(sub, body, "MeshAnchor")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        with self._stats_lock:
            stats_copy = dict(self.stats)
        subs = self._subs.list_all()
        return {
            "running": self._running,
            "destination_hash": self.destination_hash_hex,
            "display_name": self._config.display_name,
            "channels": list(self._config.channels),
            "announce_interval_sec": int(self._config.announce_interval_sec),
            "autosubscribe": bool(self._config.autosubscribe),
            "started_at_iso": (
                self._started_at.isoformat() if self._started_at else None
            ),
            "subscribers": [
                {
                    "lxmf_hash": s.lxmf_hash,
                    "added_at": s.added_at.isoformat() if s.added_at else None,
                    "last_delivery": (
                        s.last_delivery.isoformat() if s.last_delivery else None
                    ),
                    "tier": s.tier,
                    "state": s.state,
                    "consecutive_failures": s.consecutive_failures,
                    "last_failure_at": (
                        s.last_failure_at.isoformat() if s.last_failure_at else None
                    ),
                }
                for s in subs
            ],
            "subscriber_count": len(subs),
            "stats": stats_copy,
        }


def create_from_gateway_config(
    gateway_config: GatewayConfig,
    persistent_queue: Any = None,
    ack_emit_callback: Optional[Callable[[str, str], bool]] = None,
) -> Optional[LXMFBroadcastBridge]:
    """Convenience factory — returns None if the plug-in is disabled.

    Designed to be called from RNSMeshtasticBridge after LXMF setup.
    The bridge stands up its own LXMRouter (LXMF 0.9.4 caps a router
    at one delivery identity, so it can't share the gateway's).

    persistent_queue + ack_emit_callback wire Issue #66 first-caller
    behavior; both are optional and only consulted when
    lxmf_broadcast.ack_required is True.
    """
    cfg = getattr(gateway_config, "lxmf_broadcast", None)
    if cfg is None or not cfg.enabled:
        return None
    propagation = getattr(getattr(gateway_config, "rns", None), "propagation_node", "")
    return LXMFBroadcastBridge(
        broadcast_config=cfg,
        propagation_node=propagation,
        persistent_queue=persistent_queue,
        ack_emit_callback=ack_emit_callback,
    )


# ─────────────────────────────────────────────────────────────────────
# Module-level active-bridge accessor.
#
# Same pattern as gateway.meshcore_handler.get_active_handler — at most
# one broadcast bridge is active per daemon process. config_api needs
# read/write access (status + add-subscriber) without import-cycle
# tangles, so the bridge registers itself on start() and clears on stop().
# ─────────────────────────────────────────────────────────────────────

_active_bridge: Optional["LXMFBroadcastBridge"] = None
_active_bridge_lock = threading.Lock()


def _set_active_bridge(bridge: "LXMFBroadcastBridge") -> None:
    global _active_bridge
    with _active_bridge_lock:
        _active_bridge = bridge


def _clear_active_bridge(bridge: "LXMFBroadcastBridge") -> None:
    """Clear only if the caller is the currently-registered bridge.

    Identity-guarded so a stale stop() from a previous bridge can't
    clobber a freshly-started replacement.
    """
    global _active_bridge
    with _active_bridge_lock:
        if _active_bridge is bridge:
            _active_bridge = None


def get_active_broadcast_bridge() -> Optional["LXMFBroadcastBridge"]:
    """Return the currently-running LXMFBroadcastBridge, or None."""
    with _active_bridge_lock:
        return _active_bridge
