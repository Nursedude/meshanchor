"""
Base class for gateway message handlers (ABC).

All gateway handlers (Meshtastic, MQTT, MeshCore) share a common constructor
signature and interface. This ABC codifies that contract and provides shared
concrete methods to eliminate duplication.
"""

from abc import ABC, abstractmethod
import hashlib
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from utils.defaults import MAX_MESHTASTIC_MSG_LENGTH

if TYPE_CHECKING:
    from .bridge_health import BridgeHealthMonitor
    from .config import GatewayConfig
    from .node_tracker import UnifiedNodeTracker

logger = logging.getLogger(__name__)


def _strip_bridge_tags(text: str) -> str:
    """Remove LEADING bridge tags ([Mesh:..] / [RNS:..] / ...) iteratively.

    Normalization helper for RecentRfTxRegistry (mirror of MeshForge
    f02ad82): the same logical content appears raw on one path and tagged
    on another, so tag stripping is what lets the two match. Stops at the
    first non-tag text or a malformed tag (no closing bracket).
    """
    from .config import ECHO_LOOP_INVARIANT_PREFIXES
    prefixes = tuple(ECHO_LOOP_INVARIANT_PREFIXES)
    out = text.lstrip()
    while out.startswith(prefixes):
        close = out.find(']')
        if close < 0:
            break
        out = out[close + 1:].lstrip()
    return out


class RecentRfTxRegistry:
    """Cross-subsystem "recently transmitted on the primary radio" registry.

    Mirror of MeshForge f02ad82/1494e8f. Two designed paths can put the SAME
    logical content on a gateway's primary radio seconds apart: the local
    mesh_bridge cross-preset forward, and a peer gateway's Mesh→RNS relay
    arriving back via the rns_bridge as a tagged toradio/MQTT broadcast.
    Unconditional suppression of the relay loses messages (MeshForge live
    trace: ~40% of relayed events arrived ONLY via RNS — the local radio
    missed them on RF). This registry implements the safe middle: each path
    registers what it actually transmitted; the other suppresses its copy
    only on a hit.

    Keys are content-normalized (leading bridge tags stripped, whitespace
    collapsed, sha256) so ``[RNS:xx] hello`` matches the raw ``hello``.
    Thread-safe; entries expire lazily at ``max_age_s``; bounded by
    ``max_entries`` (oldest evicted).
    """

    def __init__(self, max_entries: int = 512, max_age_s: float = 300.0):
        self._max_entries = max_entries
        self._max_age_s = max_age_s
        self._entries: Dict[str, float] = {}   # key -> time.monotonic()
        self._lock = threading.Lock()

    @staticmethod
    def _key(text: str) -> Optional[str]:
        normalized = " ".join(_strip_bridge_tags(text or "").split())
        if not normalized:
            return None
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self._max_age_s
        stale = [k for k, ts in self._entries.items() if ts < cutoff]
        for k in stale:
            del self._entries[k]
        while len(self._entries) > self._max_entries:
            oldest = min(self._entries, key=self._entries.get)
            del self._entries[oldest]

    def register(self, text: str) -> None:
        """Record that ``text`` was just transmitted on the radio."""
        key = self._key(text)
        if key is None:
            return
        now = time.monotonic()
        with self._lock:
            self._entries[key] = now
            self._prune_locked(now)

    def seen_within(self, text: str, window_s: float) -> bool:
        """True if equivalent content was registered in the last window_s."""
        key = self._key(text)
        if key is None:
            return False
        now = time.monotonic()
        with self._lock:
            ts = self._entries.get(key)
            return ts is not None and (now - ts) <= window_s


# Process-wide instance — bridges are built independently with no shared
# object, so the registry is a module singleton both sides resolve at use
# time via get_rf_tx_registry() (tests monkeypatch the module global).
_rf_tx_registry = RecentRfTxRegistry()


def get_rf_tx_registry() -> RecentRfTxRegistry:
    """The process-wide seen-on-RF registry for the PRIMARY radio's mesh.

    Seen-on-RF semantics (mirror of MeshForge b645fa7): entries are
    registered on RX as well as TX — content heard on the mesh was put
    there by ANOTHER box's radio, which this box's own TX bookkeeping can
    never see. "In the registry" means "this content is on that mesh
    right now, whoever transmitted it." Suppress-only-on-hit fallback is
    preserved: if the local radio MISSED the RF copy, nothing registered
    and the relay copy still delivers.
    """
    return _rf_tx_registry


# Secondary-radio scope (mesh_bridge's serial leg). Separate module global
# rather than a dict-of-scopes so tests that monkeypatch _rf_tx_registry
# keep working unchanged. (Mirror of MeshForge b645fa7.)
_rf_secondary_registry = RecentRfTxRegistry()


def get_secondary_rf_registry() -> RecentRfTxRegistry:
    """The process-wide seen-on-RF registry for the SECONDARY radio's mesh.

    Content-keying is per-mesh: a forward INTO the secondary mesh must
    check only what is on the SECONDARY mesh — consulting the primary
    registry there would suppress every primary→secondary forward of
    content just heard on primary (i.e. break bridging entirely).
    """
    return _rf_secondary_registry


def dual_path_dedup_enabled(config: Any) -> bool:
    """Strict read of rns.dual_path_dedup_enabled (default False).

    Shared by the dispatch-time re-check in the handlers' queue dispatch
    callbacks (mirror of MeshForge 2d205b7: the enqueue-side check races
    mesh_bridge's RF-TX registration by ~250ms on LAN-fast RNS relays; by
    dispatch time — past the TX pacing — the registry is settled). Same
    ``is True`` discipline as the bridge-side helpers: MagicMock test
    configs and malformed values read as OFF.
    """
    rns_cfg = getattr(config, 'rns', None)
    return getattr(rns_cfg, 'dual_path_dedup_enabled', False) is True


def dual_path_dedup_window_s(config: Any) -> float:
    """rns.dual_path_dedup_window_sec with a safe 60s default."""
    rns_cfg = getattr(config, 'rns', None)
    try:
        return float(getattr(rns_cfg, 'dual_path_dedup_window_sec', 60))
    except (TypeError, ValueError):
        return 60.0


def chunk_for_mesh(message: str,
                   max_bytes: int = MAX_MESHTASTIC_MSG_LENGTH,
                   prefix: str = "") -> List[str]:
    """Split text into UTF-8-byte-bounded chunks for Meshtastic TX.

    Ported from MeshForge (`0066470`). Meshtastic's on-air text payload is
    capped; meshtasticd silently truncates anything larger. Multi-line bridge
    output relayed RNS→Mesh (e.g. a NomadNet message, or a bot
    ``leaderboard`` / ``wx`` reply bridged in over RNS) was cut to one packet
    by ``_truncate_if_needed``, dropping every line past the cap. This chunker
    splits such content into multiple packets instead, each guaranteed ≤
    ``max_bytes`` UTF-8 bytes, so no content is lost.

    Boundaries, in preference order: newline (keeps whole lines together),
    then word, then — only for a single word longer than the budget — a hard
    UTF-8-safe character split.

    When ``prefix`` is given (e.g. ``"[RNS:xxxx] "``), EVERY chunk carries
    it and the split budget reserves its bytes (mirror of MeshForge
    f02ad82). Tagging only chunk 0 was disproven live 2026-06-04: untagged
    tail chunks bypassed the echo-loop guards on every gateway — a
    dual-radio box re-bridged a peer's relayed tail chunks back onto its
    primary RF, and tag-adding legs overflowed the byte cap on max-size
    tails. The bridge tag is the echo-loop invariant; it must ride every
    packet, not just the first.

    Returns at least one chunk for non-empty input; never returns empty
    strings; never exceeds ``max_bytes`` for any chunk (prefix included).
    A message that already fits is returned as a single-element list.
    """
    if not message:
        return []

    def blen(s: str) -> int:
        return len(s.encode('utf-8'))

    budget = max_bytes
    if prefix:
        budget = max_bytes - blen(prefix)
        if budget < 16:
            logger.warning(
                "chunk_for_mesh: prefix %r leaves <16 bytes of budget — "
                "chunking untagged", prefix[:32])
            prefix = ""
            budget = max_bytes

    if blen(prefix) + blen(message) <= max_bytes:
        return [prefix + message]

    def char_split(token: str) -> List[str]:
        # Separator-less token longer than the budget: cut on character
        # (codepoint) boundaries so we never split a multi-byte emoji.
        out: List[str] = []
        cur = ""
        for ch in token:
            if cur and blen(cur) + blen(ch) > budget:
                out.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            out.append(cur)
        return out

    def pack(atoms: List[str], sep: str) -> List[str]:
        out: List[str] = []
        cur = ""
        for atom in atoms:
            add = blen(atom) + (blen(sep) if cur else 0)
            if cur and blen(cur) + add > budget:
                out.append(cur)
                cur = ""
            if not cur:
                if blen(atom) <= budget:
                    cur = atom
                else:
                    # Atom itself exceeds the budget — split finer: by
                    # word if it has spaces, else by character.
                    finer = pack(atom.split(' '), ' ') if ' ' in atom \
                        else char_split(atom)
                    if finer:
                        out.extend(finer[:-1])
                        cur = finer[-1]
            else:
                cur = cur + sep + atom
        if cur:
            out.append(cur)
        return out

    chunks = pack(message.split('\n'), '\n')
    if prefix:
        return [prefix + c for c in chunks]
    return chunks


class BaseMessageHandler(ABC):
    """Abstract base for network message handlers."""

    def __init__(
        self,
        config: 'GatewayConfig',
        node_tracker: 'UnifiedNodeTracker',
        health: 'BridgeHealthMonitor',
        stop_event: threading.Event,
        stats: Dict[str, Any],
        stats_lock: threading.Lock,
        message_queue,
        message_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        should_bridge: Optional[Callable] = None,
    ):
        self.config = config
        self.node_tracker = node_tracker
        self.health = health
        self._stop_event = stop_event
        self.stats = stats
        self._stats_lock = stats_lock
        self._message_queue = message_queue
        self._message_callback = message_callback
        self._status_callback = status_callback
        self._should_bridge = should_bridge
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if handler is connected."""
        return self._connected

    @abstractmethod
    def run_loop(self) -> None:
        """Main loop — blocks until stop_event is set."""
        ...

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection. Returns True on success."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect and clean up resources."""
        ...

    @abstractmethod
    def send_text(self, message: str, destination: Optional[str] = None,
                  channel: int = 0) -> bool:
        """Send a text message. Returns True on success."""
        ...

    @abstractmethod
    def queue_send(self, payload: Dict) -> bool:
        """Send from persistent queue. Returns True on success."""
        ...

    @abstractmethod
    def test_connection(self) -> bool:
        """Test if the underlying transport is reachable."""
        ...

    def _notify_status(self, status: str) -> None:
        """Notify status callback."""
        if self._status_callback:
            try:
                self._status_callback(status)
            except Exception as e:
                logger.error(f"Status callback error: {e}")

    def _truncate_if_needed(self, message: str,
                            max_length: int = MAX_MESHTASTIC_MSG_LENGTH) -> str:
        """Truncate message to byte limit if needed."""
        msg_bytes = message.encode('utf-8')
        if len(msg_bytes) > max_length:
            logger.warning(
                f"Message exceeds limit "
                f"({len(msg_bytes)} > {max_length} bytes), truncating"
            )
            truncated = msg_bytes[:max_length - 3]
            return truncated.decode('utf-8', errors='ignore') + '...'
        return message
