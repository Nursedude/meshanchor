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

import logging
import signal as _signal_mod
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils.db_helpers import connect_tuned
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


@dataclass
class Subscriber:
    """One LXMF identity opted in to the broadcast fan-out."""
    lxmf_hash: str          # 16-char hex destination hash
    added_at: datetime
    last_delivery: Optional[datetime] = None


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
            conn.commit()

    def add(self, lxmf_hash: str) -> bool:
        """Add subscriber. Returns True if newly added, False if already present."""
        lxmf_hash = lxmf_hash.lower()
        now = datetime.utcnow().isoformat()
        with self._lock, connect_tuned(self._db_path) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO subscribers(lxmf_hash, added_at) VALUES (?, ?)",
                (lxmf_hash, now),
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

    def list_all(self) -> List[Subscriber]:
        with self._lock, connect_tuned(self._db_path) as conn:
            rows = conn.execute(
                "SELECT lxmf_hash, added_at, last_delivery FROM subscribers"
            ).fetchall()
        out: List[Subscriber] = []
        for h, added, last in rows:
            out.append(
                Subscriber(
                    lxmf_hash=h,
                    added_at=_parse_iso(added) or datetime.utcnow(),
                    last_delivery=_parse_iso(last),
                )
            )
        return out

    def mark_delivered(self, lxmf_hash: str) -> None:
        lxmf_hash = lxmf_hash.lower()
        now = datetime.utcnow().isoformat()
        with self._lock, connect_tuned(self._db_path) as conn:
            conn.execute(
                "UPDATE subscribers SET last_delivery = ? WHERE lxmf_hash = ?",
                (now, lxmf_hash),
            )
            conn.commit()


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
    ) -> None:
        self._config = broadcast_config
        self._rns = rns_module if rns_module is not None else _RNS_mod
        self._lxmf = lxmf_module if lxmf_module is not None else _LXMF_mod
        self._propagation_node = (propagation_node or "").strip()

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
            self._stop_event.clear()
            if self._config.announce_interval_sec > 0:
                self._announce_thread = threading.Thread(
                    target=self._announce_loop,
                    name="LXMFBroadcast-Announce",
                    daemon=True,
                )
                self._announce_thread.start()
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

        for sub in subscribers:
            self._send_to_subscriber(sub, body, title)

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

    # Per-RPC timing: any individual rnsd RPC over the shared-instance
    # socket that takes longer than this is logged at WARNING level so
    # the next wedge produces a forensic instead of a black box. See
    # memory project_rnsd_wedging_hypothesis (audit invalidated the
    # earlier path_request-flood theory).
    _SLOW_RPC_THRESHOLD_S = 2.0

    def _timed_rpc(self, label: str, fn, *args, **kwargs):
        t0 = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except Exception:
            elapsed = time.monotonic() - t0
            logger.warning("rpc[%s] raised after %.3fs", label, elapsed)
            raise
        elapsed = time.monotonic() - t0
        if elapsed >= self._SLOW_RPC_THRESHOLD_S:
            logger.warning("rpc[%s] slow: %.3fs", label, elapsed)
        else:
            logger.debug("rpc[%s] ok %.3fs", label, elapsed)
        return result

    def _send_to_subscriber(self, sub: Subscriber, body: str, title: str) -> bool:
        if self._router is None or self._lxmf_source is None:
            return False
        RNS = self._rns
        LXMF = self._lxmf
        try:
            dest_hash = bytes.fromhex(sub.lxmf_hash)
        except ValueError:
            logger.warning("Invalid subscriber hash %r — skipping", sub.lxmf_hash)
            return False

        hash_short = sub.lxmf_hash[:8]
        try:
            if not self._timed_rpc(f"has_path[{hash_short}]",
                                   RNS.Transport.has_path, dest_hash):
                self._timed_rpc(f"request_path[{hash_short}]",
                                RNS.Transport.request_path, dest_hash)
                # Brief wait for path; do NOT block the calling thread
                # for long — the LXMF router will retry via propagation
                # if we set one in RNSConfig.
                for _ in range(30):
                    if RNS.Transport.has_path(dest_hash):
                        break
                    if self._stop_event.wait(0.1):
                        return False

            dest_identity = self._timed_rpc(
                f"identity_recall[{hash_short}]",
                RNS.Identity.recall, dest_hash,
            )
            if dest_identity is None:
                logger.debug("No identity recalled for %s — dropping fanout", sub.lxmf_hash)
                return False

            destination = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery",
            )
            lxm = LXMF.LXMessage(destination, self._lxmf_source, body, title)
            self._timed_rpc(f"handle_outbound[{hash_short}]",
                            self._router.handle_outbound, lxm)
            self._subs.mark_delivered(sub.lxmf_hash)
            with self._stats_lock:
                self.stats["fanouts"] += 1
            return True
        except Exception as e:
            logger.debug("Fanout to %s failed: %s", sub.lxmf_hash, e)
            with self._stats_lock:
                self.stats["errors"] += 1
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
        return {
            "running": self._running,
            "destination_hash": self.destination_hash_hex,
            "display_name": self._config.display_name,
            "channels": list(self._config.channels),
            "subscribers": len(self._subs.list_all()),
            "stats": stats_copy,
        }


def create_from_gateway_config(
    gateway_config: GatewayConfig,
) -> Optional[LXMFBroadcastBridge]:
    """Convenience factory — returns None if the plug-in is disabled.

    Designed to be called from RNSMeshtasticBridge after LXMF setup.
    The bridge stands up its own LXMRouter (LXMF 0.9.4 caps a router
    at one delivery identity, so it can't share the gateway's).
    """
    cfg = getattr(gateway_config, "lxmf_broadcast", None)
    if cfg is None or not cfg.enabled:
        return None
    propagation = getattr(getattr(gateway_config, "rns", None), "propagation_node", "")
    return LXMFBroadcastBridge(
        broadcast_config=cfg,
        propagation_node=propagation,
    )
