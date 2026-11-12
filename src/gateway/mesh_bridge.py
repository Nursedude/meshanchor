"""
Meshtastic-to-Meshtastic Preset Bridge

Bridges two Meshtastic networks with different LoRa presets,
enabling communication between e.g., LONG_FAST and SHORT_TURBO meshes.

Connection modes:
- TCP (legacy): Persistent TCP connection via meshtastic library.
  Blocks meshtasticd web client (port 4403 single-client limit).
- MQTT (recommended): MQTT subscription for RX, HTTP protobuf for TX.
  Zero interference — web client on :9443 works uninterrupted.

Persistence:
- SQLite-backed message queues survive restarts (via PersistentMessageQueue).

Requirements:
- Two Meshtastic radios (one per preset)
- Two meshtasticd instances on different ports
- MeshAnchor gateway configured with bridge_mode="mesh_bridge"
- For MQTT mode: mosquitto + meshtasticd mqtt.enabled=true
"""

import re
import threading
import time
import logging
import hashlib
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Callable, Any, List

from .base_handler import get_rf_tx_registry, get_secondary_rf_registry
from .canonical_message import packet_is_broadcast  # the ONE rule (F15)
from .config import (
    GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig,
    ECHO_LOOP_INVARIANT_PREFIXES,
)
from ._mesh_bridge_mqtt import MQTTMeshInterface  # re-exported (MF025 split)
from ._mesh_bridge_legs import (  # MF025 headroom; port of MF's split
    bridge_event, bump_stat, dm_destination, node_dest, queue_drop_witness,
    LINK_DOWN_EPISODE_GAP_S, LINK_DOWN_RETRY_S, MESH_BRIDGE_MAX_AGE_S,
    _library_gave_up, _write_target_gone)
from ._mesh_bridge_drain import MeshBridgeDrainMixin  # MF025 split (MF §B #23)
from .message_queue import PersistentMessageQueue, MessagePriority, RetryPolicy
from .message_queue_models import SendDeferred, SendRefused
from utils.safe_import import safe_import
from utils.paths import get_real_user_home
from utils.tx_guard import assert_iface_tx_allowed

logger = logging.getLogger(__name__)

# Import meshtastic library (optional - graceful fallback)
_meshtastic, _HAS_MESHTASTIC = safe_import('meshtastic')
_meshtastic_tcp, _HAS_MESHTASTIC_TCP = safe_import('meshtastic.tcp_interface')
_pub, _HAS_PUBSUB = safe_import('pubsub', 'pub')
# Leg machinery below ported from MeshForge BR-T #8/#10–#13 (2026-11-10). Its
# comments that name a SERIAL leg describe the MeshForge twin; MeshAnchor's
# legs are TCP and MQTT (the stream checks still cover TCPInterface, a
# StreamInterface subclass).
# How long a TCP leg waits for its endpoint's connection lock (one slot per
# meshtasticd, Issue #17) before it gives up this attempt and the leg loop retries.
_LOCK_WAIT_S = 10
# _write_target_gone / _library_gave_up live in _mesh_bridge_legs (imported above).


@dataclass
class BridgedMeshMessage:
    """Message being bridged between Meshtastic presets."""
    source_preset: str  # "longfast" or "shortturbo"
    source_id: str      # Node ID (!xxxxxxxx)
    destination_id: Optional[str]
    content: str
    channel: int = 0
    is_broadcast: bool = False
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        """Generate key for duplicate detection."""
        content_hash = hashlib.md5(self.content.encode()).hexdigest()[:8]
        return f"{self.source_id}:{content_hash}"

    def to_payload(self) -> Dict[str, Any]:
        """Serialize to dict for persistent queue storage."""
        return {
            "source_preset": self.source_preset,
            "source_id": self.source_id,
            "destination_id": self.destination_id,
            "content": self.content,
            "channel": self.channel,
            "is_broadcast": self.is_broadcast,
            "timestamp": self.timestamp.isoformat(),
            "from": self.source_id,
            "to": self.destination_id or "broadcast",
            "text": self.content,
            "type": "text",
            "metadata": self.metadata,
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> 'BridgedMeshMessage':
        """Deserialize from persistent queue payload."""
        ts = payload.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                ts = datetime.now()
        else:
            ts = datetime.now()

        return cls(
            source_preset=payload.get("source_preset", ""),
            source_id=payload.get("source_id", ""),
            destination_id=payload.get("destination_id"),
            content=payload.get("content", ""),
            channel=payload.get("channel", 0),
            is_broadcast=payload.get("is_broadcast", False),
            timestamp=ts,
            metadata=payload.get("metadata", {}),
        )


class MeshtasticPresetBridge(MeshBridgeDrainMixin):
    """
    Bridges messages between two Meshtastic networks with different presets.

    Supports both TCP (legacy) and MQTT (recommended) connection modes.
    Uses persistent SQLite queues for crash-resilient message delivery.

    Typical use case: LONG_FAST rural mesh <-> SHORT_TURBO local mesh
    """

    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or GatewayConfig.load()
        self.bridge_config = self.config.mesh_bridge

        # State
        self._running = False
        self._stop_event = threading.Event()
        self._primary_connected = False
        self._secondary_connected = False

        # Interfaces (TCPInterface or MQTTMeshInterface)
        self._primary_interface = None
        self._secondary_interface = None

        # Persistent message queues (SQLite-backed)
        queue_dir = get_real_user_home() / ".config" / "meshanchor" / "mesh_bridge_queues"
        queue_dir.mkdir(parents=True, exist_ok=True)

        retry_policy = RetryPolicy.for_meshtastic()
        self._primary_to_secondary = PersistentMessageQueue(
            db_path=str(queue_dir / "p2s.db"),
            retry_policy=retry_policy,
        )
        self._secondary_to_primary = PersistentMessageQueue(
            db_path=str(queue_dir / "s2p.db"),
            retry_policy=retry_policy,
        )

        # Register sender callbacks for queue processing
        # on_terminal: a row the queue gives up on (dead letter, expiry,
        # shed) is a named drop on the leg it came IN on.
        # BR-T #14 (MF port): a down leg DEFERS its rows (_forward_message
        # raises SendDeferred — no attempt spent), so an outage longer than
        # the retry budget no longer dead-letters them; max_age_s bounds the
        # hold, witnessed. NOT a destination-wide ready_fn (MF review F1/F2).
        self._link_down_last: Dict[str, float] = {}  # leg -> monotonic of last deferral
        self._primary_to_secondary.register_sender(
            "secondary", self._send_to_secondary,
            max_age_s=MESH_BRIDGE_MAX_AGE_S,
            on_terminal=queue_drop_witness("primary"))
        self._secondary_to_primary.register_sender(
            "primary", self._send_to_primary,
            max_age_s=MESH_BRIDGE_MAX_AGE_S,
            on_terminal=queue_drop_witness("secondary"))

        # Duplicate suppression
        self._seen_messages: Dict[str, datetime] = {}
        self._seen_lock = threading.Lock()

        # Threads
        self._primary_thread = None
        self._secondary_thread = None
        self._bridge_thread = None
        self._cleanup_thread = None

        # Callbacks
        self._message_callbacks: List[Callable] = []
        self._status_callbacks: List[Callable] = []

        # Strong references to stream-leg (serial AND tcp) pubsub listeners.
        # pypubsub holds listeners WEAKLY — a nested on_receive closure with
        # no other reference is garbage-collected right after connect and
        # the callback never fires (RX silently dead: serial 2026-06-03,
        # tcp BR-T #8 2026-11-10).
        self._stream_rx_keepalive: Dict[str, Callable] = {}
        # Same weak-listener rule for the link-lost listener (below).
        self._stream_lost_keepalive: Dict[str, Callable] = {}
        # TCP handle -> the endpoint connection lock it holds (a plain,
        # non-reentrant Lock from connection_lock_for, taken by _connect_tcp
        # for the connection's life). Each lock has at most one holder here;
        # released when THAT handle is torn down, never otherwise (BR-T #11;
        # per endpoint since #13, so two legs can hold two locks).
        self._tcp_lock_holds: Dict[Any, threading.Lock] = {}
        # Guards leg handles (_primary/_secondary_interface + _connected) and
        # _tcp_lock_holds across the leg loops, the bridge loop and the
        # library's publishing thread. Held only for swaps, never across I/O.
        # The ONLY writers of leg state are _install_leg / _take_leg /
        # _mark_leg_down (gate: TestLegStateHasOneWriter).
        self._leg_lock = threading.Lock()
        # Kind ("serial" / "tcp") of each stream leg, recorded at attach — the
        # one source for its log lines (LOST, planned close).
        self._stream_kind: Dict[str, str] = {}

        # Statistics
        self._stats_lock = threading.Lock()
        self.stats = {
            'messages_primary_to_secondary': 0,
            'messages_secondary_to_primary': 0,
            'duplicates_suppressed': 0,
            'channel_filtered': 0,
            'already_bridged_dropped': 0,
            'downlink_injected': 0,
            # Symmetric dual-path dedup (gated): primary forwards suppressed
            # because the rns_bridge's relay copy already went out on the
            # primary radio (it won the race this time).
            'dual_path_suppressed': 0,
            'errors': 0,
            # TCP legs whose link vanished (a library reconnect that failed,
            # a backstop read of isConnected) and were marked disconnected so
            # the leg loop reconnects. Pre-seeded: healthy-zero vs
            # meter-absent (honest_failure_modes #9).
            'link_lost': 0,
            'start_time': None,
        }
        # Port review r1 F3: the BR-T #4–#18 counters are pre-seeded like
        # link_lost — healthy-zero vs meter-absent (hfm #9).
        for _key in ('dm_no_destination', 'direction_filtered',
                     'queue_full_rejected', 'receive_errors',
                     'link_down_deferrals', 'loop_fatal_caught',
                     'queue_no_sender_rejected', 'queue_unsendable_rejected'):
            self.stats.setdefault(_key, 0)
        self.stats.setdefault('library_exit_caught', 0)   # MF §B #19
        # F8: one WARNING per MA-only queue rejection kind per interval.
        self._queue_reject_warned: Dict[str, float] = {}

        # Optional true-origin downlink injector for the primary (meshtasticd)
        # leg — makes cross-bridge traffic show as the real source node on
        # :9443 instead of self-TX. Default off; built only when the primary
        # leg is configured injection_mode="downlink" with a PSK.
        self._primary_downlink = self._build_downlink_injector(
            self.bridge_config.primary)
        # Origins we've already taught the primary radio about (NodeInfo
        # downlink) so it renders the friendly name, not bare hex. Injected
        # once per origin before its first text downlink.
        self._nodeinfo_sent = set()

    def _build_downlink_injector(self, leg: MeshtasticConfig):
        """Construct a DownlinkInjector for a leg, or None if not enabled."""
        if (leg.injection_mode or "toradio").lower() != "downlink":
            return None
        if not leg.downlink_psk:
            logger.warning(
                "mesh_bridge %s injection_mode=downlink but no downlink_psk — "
                "falling back to toradio", leg.name)
            return None
        try:
            from .mqtt_downlink_inject import DownlinkInjector
            injector = DownlinkInjector(
                broker=leg.mqtt_broker,
                port=leg.mqtt_port,
                channel_name=leg.mqtt_channel,
                psk_b64=leg.downlink_psk,
                root_topic="msh",
            )
            if not injector.usable:
                logger.warning(
                    "mesh_bridge %s downlink injector unusable: %s — "
                    "falling back to toradio", leg.name, injector.fatal_reason)
                return None
            logger.info(
                "mesh_bridge %s: true-origin downlink injection ENABLED "
                "(channel=%s)", leg.name, leg.mqtt_channel)
            return injector
        except Exception as e:
            logger.warning(
                "mesh_bridge %s downlink injector init failed: %s — "
                "falling back to toradio", leg.name, e)
            return None

    def _lookup_node_user(self, node_id_str: str):
        """Return {'long','short','hw'} for a node id from an interface nodedb.

        Bridged origins are heard on the source interface, whose meshtastic
        nodedb (if any) carries the NodeInfo. MeshAnchor has no serial leg —
        both legs are TCP/MQTT interfaces. A TCPInterface exposes `.nodes`; the
        MQTTMeshInterface has no nodedb, so this safely returns None there and
        NodeInfo injection is skipped (text still flows). Best-effort.
        """
        if not node_id_str:
            return None
        for iface in (self._secondary_interface, self._primary_interface):
            nodes = getattr(iface, "nodes", None)
            if not nodes:
                continue
            node = nodes.get(node_id_str)
            user = (node or {}).get("user") if isinstance(node, dict) else None
            if user and (user.get("longName") or user.get("shortName")):
                return {
                    "long": user.get("longName") or user.get("shortName") or node_id_str,
                    "short": user.get("shortName") or node_id_str[-4:],
                    "hw": user.get("hwModel"),
                }
        return None

    def _ensure_nodeinfo(self, origin: int, source_id: str) -> None:
        """Teach the primary radio this origin's NAME once (NodeInfo downlink)
        so :9443 shows 'moc2: ...' not '!ddfb8065: ...'. Best-effort: if the
        name isn't known yet we skip (and retry on a later message)."""
        if origin in self._nodeinfo_sent or self._primary_downlink is None:
            return
        user = self._lookup_node_user(source_id)
        if not user:
            return  # name not learned yet; don't mark sent — retry next time
        if self._primary_downlink.inject_nodeinfo(
            origin, user["long"], user["short"], hw_model=user["hw"],
        ):
            self._nodeinfo_sent.add(origin)
            logger.info(
                "Taught primary radio NodeInfo for !%08x (%s)",
                origin, user["long"])

    @staticmethod
    def _node_id_to_num(node_id) -> Optional[int]:
        """'!ddfb8065' / '0xddfb8065' / int → node number, or None."""
        if node_id is None:
            return None
        if isinstance(node_id, int):
            return node_id
        s = str(node_id).strip().lstrip("!")
        if s.lower().startswith("0x"):
            s = s[2:]
        try:
            return int(s, 16)
        except ValueError:
            return None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return self._primary_connected or self._secondary_connected

    def start(self) -> bool:
        """Start the mesh bridge."""
        if self._running:
            logger.warning("Mesh bridge already running")
            return True

        if not self.bridge_config.enabled:
            logger.warning("Mesh bridge not enabled in config")
            return False

        pri = self.bridge_config.primary
        sec = self.bridge_config.secondary
        pri_mode = "MQTT" if pri.use_mqtt else "TCP"
        sec_mode = "MQTT" if sec.use_mqtt else "TCP"

        # A previous run's leg thread can outlive stop()'s 5 s join (blocked in
        # the library's connect). Starting now would clear _stop_event and let
        # it resume BESIDE the new one — two loops per leg, stranded handles
        # (r4 F2). Refuse, and say why; start again once it has exited.
        alive = [t.name for t in (self._primary_thread, self._secondary_thread)
                 if t is not None and t.is_alive()]
        if alive:
            logger.error(
                f"Mesh bridge NOT started: the previous run's leg thread(s) "
                f"{alive} have not exited yet (still inside a connect) — retry "
                f"in a few seconds")
            return False

        logger.info("Starting Meshtastic preset bridge...")
        logger.info(f"  Primary: {pri.preset} @ {pri.host}:{pri.port} ({pri_mode})")
        logger.info(f"  Secondary: {sec.preset} @ {sec.host}:{sec.port} ({sec_mode})")

        self._running = True
        self._stop_event.clear()
        self.stats['start_time'] = datetime.now()

        # Start connection threads
        self._primary_thread = threading.Thread(
            target=self._primary_loop,
            daemon=True,
            name="MeshBridge-Primary"
        )
        self._primary_thread.start()

        self._secondary_thread = threading.Thread(
            target=self._secondary_loop,
            daemon=True,
            name="MeshBridge-Secondary"
        )
        self._secondary_thread.start()

        # Start bridge thread (processes persistent queues)
        self._bridge_thread = threading.Thread(
            target=self._bridge_loop,
            daemon=True,
            name="MeshBridge-Forward"
        )
        self._bridge_thread.start()

        # Start cleanup thread
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="MeshBridge-Cleanup"
        )
        self._cleanup_thread.start()

        logger.info("Mesh bridge started")
        self._notify_status("started")
        return True

    def stop(self):
        """Stop the mesh bridge."""
        if not self._running:
            return

        logger.info("Stopping mesh bridge...")
        self._running = False
        self._stop_event.set()

        # Disconnect interfaces
        self._disconnect_primary()
        self._disconnect_secondary()
        if self._primary_downlink is not None:
            self._primary_downlink.close()

        # Wait for threads
        for thread in [self._primary_thread, self._secondary_thread,
                       self._bridge_thread, self._cleanup_thread]:
            if thread and thread.is_alive():
                thread.join(timeout=5)

        # Stop queue processing if running
        try:
            self._primary_to_secondary.stop_processing()
        except Exception as e:
            logger.debug(f"Error stopping p2s queue: {e}")
        try:
            self._secondary_to_primary.stop_processing()
        except Exception as e:
            logger.debug(f"Error stopping s2p queue: {e}")

        logger.info("Mesh bridge stopped")
        self._notify_status("stopped")

    def get_status(self) -> dict:
        """Get current bridge status."""
        uptime = None
        if self.stats['start_time']:
            uptime = (datetime.now() - self.stats['start_time']).total_seconds()

        pri = self.bridge_config.primary
        sec = self.bridge_config.secondary

        return {
            'running': self._running,
            'enabled': self.bridge_config.enabled,
            'primary': {
                'connected': self._primary_connected,
                'preset': pri.preset,
                'host': pri.host,
                'port': pri.port,
                'mode': 'mqtt' if pri.use_mqtt else 'tcp',
            },
            'secondary': {
                'connected': self._secondary_connected,
                'preset': sec.preset,
                'host': sec.host,
                'port': sec.port,
                'mode': 'mqtt' if sec.use_mqtt else 'tcp',
            },
            'direction': self.bridge_config.direction,
            'uptime_seconds': uptime,
            'statistics': self.stats.copy(),
            'queue': {
                'p2s_pending': self._primary_to_secondary.get_queue_depth(),
                's2p_pending': self._secondary_to_primary.get_queue_depth(),
                # BR-T #14: rows waiting on a leg that cannot take a write —
                # read NOW (MF review F6: never a last-tick flag)
                'p2s_held_link_down': self._held_link_down(
                    self._primary_to_secondary, "secondary"),
                's2p_held_link_down': self._held_link_down(
                    self._secondary_to_primary, "primary"),
            },
        }

    def register_message_callback(self, callback: Callable):
        """Register callback for bridged messages."""
        self._message_callbacks.append(callback)

    def register_status_callback(self, callback: Callable):
        """Register callback for status changes."""
        self._status_callbacks.append(callback)

    # ========================================
    # Connection Loops
    # ========================================

    def _primary_loop(self):
        """Main loop for primary Meshtastic connection."""
        self._leg_loop("primary")

    def _secondary_loop(self):
        """Main loop for secondary Meshtastic connection."""
        self._leg_loop("secondary")

    def _leg_loop(self, leg: str):
        """One loop for both legs (they were two copies; a fix landed in one
        and not the other is how this file grew its defects)."""
        connect = getattr(self, f"_connect_{leg}")
        while self._running:
            try:
                if not getattr(self, f"_{leg}_connected"):
                    connect()
                    if not getattr(self, f"_{leg}_connected"):
                        # absent radio: retry every 10 s, not every 1 s
                        # (each failed attempt logs an ERROR)
                        if self._stop_event.wait(10):
                            break
                        continue

                # Backstop for a connection.lost this leg never heard (r2 F-A):
                # read the library's own verdict every tick.
                iface = getattr(self, f"_{leg}_interface")
                if _library_gave_up(iface):
                    logger.warning(
                        f"{leg} link: the library gave it up (isConnected "
                        f"cleared) with no connection.lost heard — treating as lost")
                    self._on_stream_link_lost(
                        leg, iface, self._stream_kind.get(leg, "stream"))
                    # no `continue`: fall through to the wait, so a claim that
                    # fails can never spin this loop hot (r3 mutant hang)

                if self._stop_event.wait(1):
                    break

            except Exception as e:
                logger.error(f"{leg.capitalize()} loop error: {e}")
                self._mark_leg_down(leg)
                if self._stop_event.wait(5):
                    break

    def _bridge_loop(self):
        """Process persistent queues and forward messages."""
        while self._running:
            try:
                processed = self._drain_once()   # MF §B #23 (_mesh_bridge_drain)

                # If no messages processed, wait a bit
                if processed == 0:
                    if self._stop_event.wait(0.2):
                        break
                else:
                    if self._stop_event.wait(0.05):
                        break

            except (Exception, SystemExit) as e:   # BR-T #18: library our_exit
                if isinstance(e, SystemExit):
                    bump_stat(self, 'loop_fatal_caught')
                logger.error(f"Bridge loop error ({type(e).__name__}): {e}")
                if self._stop_event.wait(1):
                    break

    def _cleanup_loop(self):
        """Cleanup stale dedup entries."""
        while self._running:
            try:
                if self._stop_event.wait(10):
                    break

                cutoff = datetime.now() - timedelta(
                    seconds=self.bridge_config.dedup_window_sec
                )

                with self._seen_lock:
                    expired = [k for k, v in self._seen_messages.items() if v < cutoff]
                    for key in expired:
                        del self._seen_messages[key]

                self._expire_undrained()

            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")

    # ========================================
    # Connection Management
    # ========================================

    def _connect_primary(self):
        """Connect to primary Meshtastic interface."""
        # A leg can be down while still holding its old handle — the loop's
        # except branch flips _primary_connected without a teardown. Tear it
        # down first, or a TCP leg's old handle keeps the connection lock and
        # the reconnect waits on it forever (BR-T #11, second branch).
        if self._primary_interface is not None:
            self._disconnect_primary()
        self._install_or_close("primary", *self._connect_interface(
            self.bridge_config.primary,
            "primary",
            self._on_primary_receive
        ))

    def _connect_secondary(self):
        """Connect to secondary Meshtastic interface."""
        if self._secondary_interface is not None:   # see _connect_primary
            self._disconnect_secondary()
        self._install_or_close("secondary", *self._connect_interface(
            self.bridge_config.secondary,
            "secondary",
            self._on_secondary_receive
        ))

    # ---- the ONE writer set for leg state (gate: TestLegStateHasOneWriter) --

    def _install_leg(self, leg: str, interface, connected: bool) -> bool:
        """Set ``leg``'s handle and connected flag together, under _leg_lock
        (11-10 review r2 F-B: a loss handled between two separate stores left
        connected=True with no handle — "reconnecting", forever).

        Refuses (returns False, installs nothing) once stop() has begun: stop()
        sets _stop_event BEFORE its teardown takes this lock, so a connect still
        in flight either lands first and is torn down, or sees the event here —
        never a handle installed after the teardown (r3 finding 1: socket open
        and the connection lock held after stop())."""
        with self._leg_lock:
            if interface is not None and self._stop_event.is_set():
                return False
            setattr(self, f"_{leg}_interface", interface)
            setattr(self, f"_{leg}_connected", bool(connected) and interface is not None)
            return True

    def _install_or_close(self, leg: str, interface, connected: bool) -> None:
        """Install a fresh connect, or — if stop() got there first — close it
        (releasing its connection lock) instead of leaving it to nobody."""
        if not self._install_leg(leg, interface, connected):
            self._close_taken(leg, interface)
        elif interface is not None and connected:
            # MF §B #21: AFTER the install — notified from inside _connect_*,
            # the event's own status payload read this leg as down. A failing
            # notify (get_status reads SQLite) never unseats an installed leg.
            try:
                self._notify_status(f"{leg}_connected")
            except Exception as e:  # noqa: BLE001 — status is advisory
                logger.error(f"{leg} connected, status notify failed: {e}")

    def _mark_leg_down(self, leg: str) -> None:
        """Mark ``leg`` disconnected without detaching its handle (the next
        connect tears that down first)."""
        with self._leg_lock:
            setattr(self, f"_{leg}_connected", False)

    def _connect_interface(self, config: MeshtasticConfig, name: str,
                           callback: Callable) -> tuple:
        """
        Connect to a Meshtastic interface using TCP or MQTT mode.

        When config.use_mqtt is True, uses MQTTMeshInterface (zero-interference).
        Otherwise falls back to TCP (legacy, blocks web client).
        """
        if config.use_mqtt:
            return self._connect_mqtt(config, name, callback)
        return self._connect_tcp(config, name, callback)

    def _attach_stream_listeners(self, interface, name: str,
                                 callback: Callable, kind: str) -> None:
        """RX + link-loss listeners for a meshtastic library STREAM leg
        (serial or tcp) — one implementation, so the two legs cannot drift
        apart again (the tcp leg had neither until BR-T #8/#10, 2026-11-10)."""
        our_interface = interface
        self._stream_kind[name] = kind

        def on_receive(packet, interface):
            # pubsub "meshtastic.receive" is global — filter to packets
            # from THIS interface (the other leg, or any other meshtastic
            # interface in the same process, would otherwise leak in).
            if interface is not our_interface:
                return
            callback(packet)

        # pypubsub holds listeners weakly — retain a strong reference
        # or the closure is garbage-collected and RX silently dies.
        self._stream_rx_keepalive[name] = on_receive
        _pub.subscribe(on_receive, "meshtastic.receive")

        # Link loss. Without this the leg stays "connected" on a dead handle:
        # serial — moc 2026-09-27, the V3 was unplugged at 08:36, the loop
        # never reconnected, and the first sign was "Failed to forward to
        # secondary" 5.7 h later; tcp — a failed library _reconnect() leaves
        # socket=None and every later write is a silent no-op (BR-T #10).
        # The library publishes connection.lost when its reader thread exits.
        def on_lost(interface=None):
            if interface is not our_interface:
                return
            self._on_stream_link_lost(name, our_interface, kind)

        self._stream_lost_keepalive[name] = on_lost
        _pub.subscribe(on_lost, "meshtastic.connection.lost")

    def _on_stream_link_lost(self, name: str, interface, kind: str) -> None:
        """A stream leg's link went away: mark THAT leg disconnected so its
        loop reconnects, and leave a witness. Identity-matched, so a late
        event from an interface already replaced changes nothing.

        The log line ``<leg> <kind> link LOST`` is parsed by
        ``scripts/c21_bridge_run.py`` (``_LOST_RE``) — change both together."""
        if interface is self._secondary_interface:
            leg = "secondary"
        elif interface is self._primary_interface:
            leg = "primary"
        else:
            return
        if self._stop_event.is_set():
            # stop() closing the port fires connection.lost: planned, not lost
            logger.info(f"{leg} {kind} link closed ({name}) — bridge stopping")
            return
        # Claim the leg atomically BEFORE counting: a second teardown arriving
        # while this one is inside close() (the library's publishing thread vs
        # a leg loop) finds it already taken — one loss, one count, one close
        # (11-10 review F2: link_lost=2 for one loss).
        taken = self._take_leg(leg, expected=interface)
        if taken is None:
            return
        logger.warning(
            f"{leg} {kind} link LOST ({name}) — link gone; reconnecting")
        with self._stats_lock:
            self.stats['link_lost'] += 1
        self._close_taken(leg, taken)
        self._notify_status(f"{leg}_disconnected")

    def _connect_mqtt(self, config: MeshtasticConfig, name: str,
                      callback: Callable) -> tuple:
        """Connect via MQTT (zero-interference mode)."""
        try:
            logger.info(
                f"Connecting to {name} via MQTT "
                f"({config.mqtt_broker}:{config.mqtt_port})"
            )

            interface = MQTTMeshInterface(
                config=config,
                name=name,
                message_callback=callback,
                stop_event=self._stop_event,
            )

            if interface.connect():
                logger.info(f"Connected to {name} via MQTT ({config.preset})")
                return interface, True

            logger.warning(f"Failed to connect {name} via MQTT")
            return None, False

        except Exception as e:
            logger.error(f"Failed to connect {name} via MQTT: {e}")
            return None, False

    def _connect_tcp(self, config: MeshtasticConfig, name: str,
                     callback: Callable) -> tuple:
        """Connect via TCP (legacy mode — contends with web client at :9443).

        WARNING: Direct TCPInterface creation competes for meshtasticd's single
        TCP slot (Issue #17). Prefer MQTT mode or HTTP send_text_direct() for TX.
        This method acquires the global connection lock to prevent thrashing.
        """
        if not _HAS_MESHTASTIC or not _HAS_MESHTASTIC_TCP or not _HAS_PUBSUB:
            logger.error("Meshtastic library not installed")
            return None, False

        try:
            logger.warning(
                f"TCP legacy mode for {name} — this contends with :9443 web client. "
                "Consider switching to MQTT mode (see mesh_bridge config)."
            )

            from utils.meshtastic_connection import (
                connection_lock_for, wait_for_cooldown
            )
            from utils.service_check import check_service

            # Advisory pre-flight: warn if meshtasticd not detected (Issue #3)
            status = check_service('meshtasticd')
            if not status.available:
                logger.warning("meshtasticd pre-flight: %s (attempting connection anyway)", status.message)

            # THIS port's lock (BR-T #13): meshtasticd serves one TCP client
            # (Issue #17) and a daemon listens on one port, so the lock is per
            # PORT — the default two-TCP-leg bridge (4403 + 4404) connects both
            # legs; 4403 is still MESHTASTIC_CONNECTION_LOCK.
            endpoint = f"{config.host}:{config.port}"
            conn_lock = connection_lock_for(config.host, config.port)
            if not conn_lock.acquire(timeout=_LOCK_WAIT_S):
                with self._leg_lock:     # read the holds map under its lock (r4 F4)
                    ours = any(h is conn_lock for h in self._tcp_lock_holds.values())
                other = "the OTHER bridge leg" if ours else "another component"
                # The lock is per PORT (connection_lock_for): say so, and the
                # way out — the operator cannot see a lock.
                logger.error(
                    f"TCP connection lock for {endpoint} busy — {other} holds the "
                    f"lock for TCP port {config.port} (one meshtasticd client per "
                    f"port, Issue #17)"
                    + (" — both legs use that port: if they are the same radio, "
                       "check mesh_bridge primary/secondary host+port; if they "
                       "are two radios, give them different ports or use MQTT"
                       if ours else ""))
                return None, False

            interface = None
            try:
                wait_for_cooldown()
                # portNumber: the leg's OWN port. Omitted, the library default
                # (4403) connected a :4404 leg to the other leg's radio (BR-T #12).
                interface = _meshtastic_tcp.TCPInterface(
                    hostname=config.host, portNumber=config.port)
                with self._leg_lock:
                    self._tcp_lock_holds[interface] = conn_lock
                # Held listener + interface filter + link-loss (BR-T #8/#10):
                # the bare closure here was garbage-collected after connect.
                self._attach_stream_listeners(interface, name, callback, "tcp")

                logger.info(f"Connected to {name} via TCP ({config.preset})")
                # Note: lock stays held — released when THIS handle is torn down
                return interface, True
            except Exception:
                # Anything after construction raised (11-10 review F5): close
                # the half-made handle, forget it as owner, THEN free the lock —
                # never leave an open radio connection or a stale owner behind.
                if interface is not None:
                    try:
                        interface.close()
                    except Exception as close_err:
                        logger.debug(f"Error closing half-made {name}: {close_err}")
                    with self._leg_lock:
                        self._tcp_lock_holds.pop(interface, None)
                conn_lock.release()
                raise

        except Exception as e:
            logger.error(f"Failed to connect to {name} via TCP: {e}")
            return None, False

    def _take_leg(self, leg: str, expected=None):
        """Atomically detach ``leg``'s interface and mark the leg down; return
        it, or None if there is nothing to take (or it is no longer
        ``expected``). Exactly one caller gets a given handle, so teardown runs
        once however many threads race to it (11-10 review F2)."""
        with self._leg_lock:
            current = getattr(self, f"_{leg}_interface")
            if current is None or (expected is not None and current is not expected):
                return None
            setattr(self, f"_{leg}_interface", None)
            setattr(self, f"_{leg}_connected", False)
            return current

    def _close_taken(self, leg: str, interface) -> None:
        """Close a handle _take_leg returned, then free the TCP lock if it held it."""
        if self._stop_event.is_set() and leg in self._stream_kind:
            # stop(): the close is planned. Said HERE, because the
            # connection.lost it fires arrives after the handle is taken and so
            # never reaches _on_stream_link_lost (11-10 review r2 F-C).
            logger.info(f"{leg} {self._stream_kind[leg]} link closed ({leg}) "
                        f"— bridge stopping")
        try:
            interface.close()
        except Exception as e:
            logger.debug(f"Error closing {leg}: {e}")
        self._release_tcp_lock_if_owner(interface)

    def _disconnect_primary(self):
        """Disconnect primary interface."""
        taken = self._take_leg("primary")
        if taken is not None:
            self._close_taken("primary", taken)
        self._mark_leg_down("primary")

    def _disconnect_secondary(self):
        """Disconnect secondary interface."""
        taken = self._take_leg("secondary")
        if taken is not None:
            self._close_taken("secondary", taken)
        self._mark_leg_down("secondary")

    def _release_tcp_lock_if_owner(self, interface) -> None:
        """Release the endpoint lock ``interface`` took in _connect_tcp, iff it
        holds one (BR-T #11/#13). A plain Lock: never released, the next
        reconnect waited 10 s on this process's own hold and failed, forever;
        released for the wrong handle, it would free ANOTHER holder's slot.
        Lookup-and-forget is one step under _leg_lock."""
        if interface is None:
            return
        with self._leg_lock:
            held = self._tcp_lock_holds.pop(interface, None)
        if held is None:
            return
        try:
            held.release()
        except RuntimeError:
            # already unlocked: someone released it out from under us — say so
            logger.warning("TCP connection lock was already released "
                           "(leg teardown found it unlocked)")

    # ========================================
    # Message Processing
    # ========================================

    def _on_primary_receive(self, packet: dict):
        """Handle message from primary interface."""
        self._process_receive(packet, "primary", "secondary",
                              self._primary_to_secondary)

    def _on_secondary_receive(self, packet: dict):
        """Handle message from secondary interface."""
        self._process_receive(packet, "secondary", "primary",
                              self._secondary_to_primary)

    def _process_receive(self, packet: dict, source: str, dest: str,
                         queue: PersistentMessageQueue):
        """Process received message and enqueue for forwarding."""
        text = ""
        msg_id = None
        try:
            decoded = packet.get('decoded', {})
            portnum = decoded.get('portnum')

            # Only bridge text messages
            if portnum != 'TEXT_MESSAGE_APP':
                return

            # Channel allow-list: only bridge text on allow-listed channel
            # indexes (empty list = all). RX hears every channel of its radio;
            # without this, a secondary's ch0 text would be re-TXed on the
            # primary's ch0 (often a public channel).
            allowed = self.bridge_config.channels
            channel = packet.get('channel', 0)
            if allowed and channel not in allowed:
                with self._stats_lock:
                    self.stats['channel_filtered'] += 1
                logger.debug(
                    f"Channel filter: dropped ch{channel} text from {source} "
                    f"(allow-list: {allowed})"
                )
                return

            from_id = packet.get('fromId', '')
            to_id = packet.get('toId', '')

            # Broadcast detection must cover every shape meshtastic emits:
            #   - serial/TCP lib maps 0xFFFFFFFF -> '^all' (BROADCAST_ADDR)
            #   - MQTT json path here formats it as '!ffffffff'
            #   - the raw numeric `to` field (0xFFFFFFFF, or absent = 0)
            # Getting this wrong sends broadcasts as DMs and skips the
            # broadcast-only downlink-injection path (moc canary, 2026-06-03).
            # The ONE rule (F15): numeric `to` wins; text ids are a fallback.
            is_broadcast = packet_is_broadcast(packet)

            payload = decoded.get('payload', b'')
            if isinstance(payload, bytes):
                text = payload.decode('utf-8', errors='ignore')
            else:
                text = str(payload)
            bridge_event("rx", source, text)

            # Seen-on-RF registration (mirror of MeshForge b645fa7,
            # cross-BOX dedup direction): a broadcast heard on this leg IS
            # on that radio's mesh, whoever TX'd it — including a peer
            # box's radio on the same RF segment. Scope matters: register
            # into the RECEIVING leg's registry only (a primary-RX entry
            # must never suppress a primary→secondary forward of the same
            # content). Deliberately BEFORE the already-bridged drop below
            # — tagged content is refused for re-bridging but is still on
            # the mesh. After the channel allow-list, so filtered channels
            # don't poison the registry.
            if is_broadcast and text:
                (get_rf_tx_registry() if source == "primary"
                 else get_secondary_rf_registry()).register(text)

            # ECHO-LOOP INVARIANT: content already carrying a bridge tag has
            # crossed SOME bridge — it never crosses another. Live failure
            # shape (moc, 2026-06-03): ST text -> mesh_bridge -> HAT -> M->R
            # fan-out -> peer gateway (also on the ST RF segment) re-injected
            # it tagged [RNS:...] -> our RX heard it -> re-forwarded with a
            # fresh prefix -> dedup never matched the mutated content ->
            # infinite prefix-growing amplification. Our own forwards carry a
            # [Mesh: prefix (ECHO_LOOP_INVARIANT_PREFIXES), so every gateway —
            # including this one — refuses them on re-RX.
            if text.lstrip().startswith(tuple(ECHO_LOOP_INVARIANT_PREFIXES)):
                with self._stats_lock:
                    self.stats['already_bridged_dropped'] += 1
                logger.debug(
                    f"Already-bridged content dropped from {source}: "
                    f"{text[:50]}..."
                )
                return bridge_event("drop", source, text, "already_bridged")

            # Skip if message matches exclude filter
            if self.bridge_config.exclude_filter:
                try:
                    if re.search(self.bridge_config.exclude_filter, text):
                        return bridge_event("drop", source, text, "exclude_filter")
                except re.error:
                    pass

            # Skip if message filter set and doesn't match
            if self.bridge_config.message_filter:
                try:
                    if not re.search(self.bridge_config.message_filter, text):
                        return bridge_event("drop", source, text, "message_filter")
                except re.error:
                    pass

            if not is_broadcast:                       # BR-T #18: read ONCE (r1 F9)
                to_id = dm_destination(packet)
                if to_id is None:
                    bump_stat(self, 'dm_no_destination')
                    return bridge_event("drop", source, text, "dm_no_destination")
            preset = (self.bridge_config.primary.preset if source == "primary"
                      else self.bridge_config.secondary.preset)

            msg = BridgedMeshMessage(
                source_preset=preset,
                source_id=from_id,
                destination_id=to_id,
                content=text,
                channel=packet.get('channel', 0),
                is_broadcast=is_broadcast,
                metadata={
                    'snr': packet.get('rxSnr'),
                    'rssi': packet.get('rxRssi'),
                    'source_interface': source,
                }
            )

            # Check for duplicate
            if self._is_duplicate(msg):
                with self._stats_lock:
                    self.stats['duplicates_suppressed'] += 1
                return bridge_event("drop", source, text, "duplicate")

            # BR-T #4: a one-way bridge refuses the other direction HERE, named.
            # Before, only _bridge_loop checked, so the row sat pending in a
            # queue nothing drains (a DM, HIGH priority, was never even shed).
            # LAST before enqueue (MF review F3): every filter and echo-loop
            # check above still sees this leg's traffic.
            if not self._direction_allows(source):
                bump_stat(self, 'direction_filtered')
                return bridge_event("drop", source, text, "direction")

            # Enqueue in persistent queue
            priority = (MessagePriority.HIGH if not msg.is_broadcast
                        else MessagePriority.NORMAL)
            msg_id, rejected = queue.enqueue_with_reason(
                payload=msg.to_payload(),
                destination=dest,
                priority=priority,
                # BIC-2: a transport, never the interface name ("primary" /
                # "secondary" is outside the leg vocabulary and reads "?").
                source="meshtastic",
            )

            if msg_id is None:
                # BR-T #6: a full queue is a LOSS, not a repeat — its own name
                # and counter (the queue's own shed_rejected is per-queue).
                if rejected == PersistentMessageQueue.REJECT_QUEUE_FULL:
                    bump_stat(self, 'queue_full_rejected')
                    logger.warning(f"Message from {source} REJECTED: "
                                   f"queue to {dest} full — not bridged")
                    return bridge_event("drop", source, text, "queue_full")
                if rejected != PersistentMessageQueue.REJECT_DEDUP:
                    # MA-only: the queue's no_sender / unsendable refusals are
                    # losses too — counted and named, never read as a harmless
                    # repeat; the WARNING is throttled like the queue's own
                    # (r1 F8: one line per message flooded the log).
                    bump_stat(self, f'queue_{rejected}_rejected')
                    now = time.monotonic()
                    last = self._queue_reject_warned.get(str(rejected))
                    if (last is None or now - last
                            > PersistentMessageQueue.NO_SENDER_LOG_INTERVAL):
                        self._queue_reject_warned[str(rejected)] = now
                        logger.warning(
                            f"Message from {source} REJECTED by the queue to "
                            f"{dest} ({rejected}) — not bridged (further "
                            f"rejections counted in queue_{rejected}_rejected)")
                    return bridge_event("drop", source, text, str(rejected))
                logger.debug(f"Message from {source} deduplicated by queue")
                return bridge_event("drop", source, text, "queue_dedup")

            # Notify callbacks
            self._notify_message(msg)

        except Exception as e:
            # BR-T #5: a swallow gets a witness (hfm #9) — a counter and a
            # named drop, not an ERROR line alone.
            bump_stat(self, 'receive_errors')
            logger.error(f"Error processing message from {source}: {e}")
            if msg_id is None:         # MF review F8: a queued row is not a loss
                bridge_event("drop", source, text, "receive_error")

    def _is_duplicate(self, msg: BridgedMeshMessage) -> bool:
        """Check if message was recently seen (loop prevention)."""
        key = msg.dedup_key

        with self._seen_lock:
            if key in self._seen_messages:
                return True

            self._seen_messages[key] = datetime.now()
            return False

    # ========================================
    # Queue Send Callbacks
    # ========================================

    def _send_to_secondary(self, payload: Dict) -> bool:
        """Send callback for persistent queue — forward to secondary."""
        msg = BridgedMeshMessage.from_payload(payload)

        # Seen-on-RF dedup, secondary scope (gated, default off) — mirror
        # of MeshForge b645fa7: when this content is already on the
        # SECONDARY mesh (e.g. a peer gateway's relay injected it there and
        # our RX heard it), suppress the forward instead of
        # double-delivering. Checks the secondary registry ONLY: consulting
        # the primary one here would suppress every primary→secondary
        # forward of content just heard on primary.
        if (msg.is_broadcast and self._dual_path_dedup_on()
                and get_secondary_rf_registry().seen_within(
                    msg.content, self._dual_path_dedup_window())):
            with self._stats_lock:
                self.stats['dual_path_suppressed_secondary'] = (
                    self.stats.get('dual_path_suppressed_secondary', 0) + 1)
            logger.info(
                f"mesh_bridge secondary forward suppressed (seen-on-RF "
                f"dedup — already on secondary mesh): {msg.content[:50]}...")
            bridge_event("drop", "primary", msg.content, "dual_path")
            return True

        success = self._forward_message(
            msg, self._secondary_interface,
            self._secondary_connected, "secondary"
        )
        if success:
            with self._stats_lock:
                self.stats['messages_primary_to_secondary'] += 1
            # Mirror of _send_to_primary's registration, secondary scope.
            if msg.is_broadcast:
                get_secondary_rf_registry().register(msg.content)
        return success

    def _dual_path_dedup_on(self) -> bool:
        """Strict read of rns.dual_path_dedup_enabled (default False).

        ``is True`` deliberately (MeshForge gate discipline) — MagicMock
        test configs and malformed values read as OFF.
        """
        rns_cfg = getattr(self.config, 'rns', None)
        return getattr(rns_cfg, 'dual_path_dedup_enabled', False) is True

    def _dual_path_dedup_window(self) -> float:
        rns_cfg = getattr(self.config, 'rns', None)
        try:
            return float(getattr(rns_cfg, 'dual_path_dedup_window_sec', 60))
        except (TypeError, ValueError):
            return 60.0

    def _send_to_primary(self, payload: Dict) -> bool:
        """Send callback for persistent queue — forward to primary."""
        msg = BridgedMeshMessage.from_payload(payload)

        # Symmetric dual-path dedup (gated, default off; mirror of MeshForge
        # f02ad82): when the rns_bridge's relay copy of this same content
        # WON the race and already went out on the primary radio (it
        # registered on TX), suppress our forward instead of
        # double-delivering. Returning True marks the queue item delivered —
        # the content IS on the radio, just via the other path.
        if (msg.is_broadcast and self._dual_path_dedup_on()
                and get_rf_tx_registry().seen_within(
                    msg.content, self._dual_path_dedup_window())):
            with self._stats_lock:
                self.stats['dual_path_suppressed'] += 1
            logger.info(
                f"mesh_bridge forward suppressed (dual-path dedup — already "
                f"on RF via rns_bridge): {msg.content[:50]}...")
            bridge_event("drop", "secondary", msg.content, "dual_path")
            return True

        success = self._forward_message(
            msg, self._primary_interface,
            self._primary_connected, "primary"
        )
        if success:
            with self._stats_lock:
                self.stats['messages_secondary_to_primary'] += 1
            # Dual-path dedup: record what just went onto the primary radio
            # so the rns_bridge's R→M path (the peer-relay copy of this same
            # content, arriving seconds later via RNS) can suppress its
            # duplicate. Broadcasts only — DMs never dual-path.
            if msg.is_broadcast:
                get_rf_tx_registry().register(msg.content)
        return success

    def _leg_can_write(self, leg: str) -> bool:
        """The two facts _forward_message defers on — the connected flag and
        the write target the library gates on (#10) — read as one pair under
        _leg_lock (MF review F7). Status only; the send path reads its own."""
        with self._leg_lock:
            iface = getattr(self, f"_{leg}_interface")
            connected = getattr(self, f"_{leg}_connected")
        return bool(connected and iface is not None
                    and not _write_target_gone(iface))

    def _defer_link_down(self, dest_name: str, why: str):
        """BR-T #14: a down leg is not a send error — the row waits, retry
        budget untouched. Logged LOUD once per outage, quiet per re-ask."""
        bump_stat(self, 'link_down_deferrals')
        self._note_link_down(dest_name, why)
        raise SendDeferred(LINK_DOWN_RETRY_S, f"{dest_name} link down: {why}")

    def _note_link_down(self, dest_name: str, why: str) -> None:
        """The outage episode log: ERROR once per outage, DEBUG per re-ask."""
        now = time.monotonic()
        last = self._link_down_last.get(dest_name)
        self._link_down_last[dest_name] = now
        if last is None or now - last > LINK_DOWN_EPISODE_GAP_S:
            logger.error(f"Forward to {dest_name} DEFERRED: link down ({why}) "
                         f"— held, not sent; retried every {LINK_DOWN_RETRY_S:.0f} s "
                         f"for up to {MESH_BRIDGE_MAX_AGE_S} s")
        else:
            logger.debug(f"Forward to {dest_name} still deferred: {why}")

    def _forward_message(self, msg: BridgedMeshMessage, interface,
                         connected: bool, dest_name: str) -> bool:
        """Forward a message to the specified interface."""
        if not msg.is_broadcast and node_dest(msg.destination_id) is None:  # #18 r3
            bump_stat(self, 'dm_no_destination')
            logger.error(f"Forward to {dest_name} REFUSED: DM with no destination "
                         f"(never sendText(destinationId=None)): {msg.content[:50]}")
            # Port review r1 F2: permanent — dead-letter on THIS attempt
            # (MA's SendRefused), never a retryable False that re-counts it.
            raise SendRefused("dm_no_destination")
        try:
            # Format message with prefix if enabled
            content = msg.content
            if self.bridge_config.add_prefix:
                prefix = self.bridge_config.prefix_format.format(
                    source_preset=msg.source_preset,
                    source_id=msg.source_id[-4:] if msg.source_id else "????",
                )
                content = prefix + content

            # True-origin downlink injection (primary/meshtasticd leg only).
            # Makes the forward show as the REAL source node on :9443 instead
            # of self-TX. Only for broadcasts (downlink is a channel inject);
            # any failure falls through to the toradio sendText path below so
            # a message is never dropped.
            #
            # The downlink path injects the RAW message (no [Mesh:...] prefix):
            # the true-origin attribution already names the sender, so the tag
            # is just clutter ("moc2: [Mesh:SHORT_TURBO:8065] hi" -> "moc2: hi").
            # The prefix exists only as the cross-gateway echo-loop guard for
            # the toradio path (where re-heard json could re-bridge). Downlink
            # is loop-safe by construction: meshtasticd does NOT re-uplink a
            # via_mqtt packet to json and the gateway can't decode the raw -e-
            # protobuf, so the injected packet never re-enters the bridge
            # (proven step-0 + moc canary 2026-06-03). The toradio FALLBACK
            # below still sends the tagged `content`, so loop safety is intact
            # whenever downlink is unavailable.
            if (dest_name == "primary" and self._primary_downlink is not None
                    and msg.is_broadcast):
                origin = self._node_id_to_num(msg.source_id)
                if origin is not None:
                    # Teach the radio the origin's name first, so its text
                    # renders as "moc2: ..." instead of bare hex.
                    self._ensure_nodeinfo(origin, msg.source_id)
                if origin is not None and self._primary_downlink.inject(
                    msg.content, origin, hop_limit=3,
                ):
                    with self._stats_lock:
                        self.stats['downlink_injected'] += 1
                    logger.info(
                        f"Bridged {msg.source_preset} -> {dest_name} "
                        f"(downlink as !{origin:08x}): {msg.content[:50]}...")
                    bridge_event("tx", dest_name, msg.content)
                    self._link_down_last.pop(dest_name, None)
                    return True
                logger.debug(
                    "Downlink inject unavailable for %s — toradio fallback",
                    dest_name)

            # Send to interface (works for TCP and MQTT interfaces).
            # Broadcasts OMIT destinationId so each interface uses its own
            # broadcast default — meshtastic's TCPInterface hard-exits the
            # process (our_exit) on destinationId=None, while its default is
            # BROADCAST_ADDR ('^all').
            # BR-T #10 (2026-11-10): "connected" is a flag; the write target is
            # a fact. In the window before the library's connection.lost lands
            # (or during its own 1 s _reconnect), its write is a silent no-op
            # that still returns a packet. Measure it and REFUSE — the queue
            # retries. Do NOT tear the leg down here: closing an interface the
            # library is mid-reconnect on leaves its reader to open a socket
            # nobody owns (11-10 review F1); connection.lost is the ONE
            # teardown signal. After the downlink branch: that path goes via
            # the broker, not the socket (F4). Residual, UNKNOWN by design: a
            # write racing a mid-call socket drop cannot be seen from here.
            # MF §B #22: the not-connected defer sits HERE, after the downlink
            # branch — a broadcast the broker path can carry is not held.
            if not connected or not interface:
                self._defer_link_down(dest_name, "not connected")
            if _write_target_gone(interface):
                self._defer_link_down(
                    dest_name, f"{type(interface).__name__} has no open "
                    f"socket/stream — the library would drop this write silently")

            assert_iface_tx_allowed(
                interface, kind="tcp_sendtext",
                detail=f"mesh_bridge cross-preset forward -> {dest_name}")
            if msg.is_broadcast:
                sent = interface.sendText(content, channelIndex=msg.channel)
            else:
                sent = interface.sendText(
                    content,
                    destinationId=msg.destination_id,
                    channelIndex=msg.channel
                )

            # BR-T #1 (port of MeshForge 6fe286ef, 2026-11-10): a write the
            # interface REFUSED is not a send. MQTTMeshInterface.sendText
            # returns False when the HTTP send fails, the text is over the
            # on-air budget, or the protobuf client is unavailable; that value
            # used to be discarded, so the failure got a "Bridged" line, a
            # delivered counter and a cleared queue row. Only the declared
            # failure value is read as failure — the meshtastic library
            # returns a packet object on success.
            if sent is False:
                logger.error(
                    f"Forward to {dest_name} REFUSED by the interface "
                    f"(sendText returned False) — not sent: {content[:50]}...")
                with self._stats_lock:
                    self.stats['errors'] += 1
                return False

            logger.info(f"Bridged {msg.source_preset} -> {dest_name}: {content[:50]}...")
            bridge_event("tx", dest_name, content)
            self._link_down_last.pop(dest_name, None)
            return True

        except SendDeferred:
            raise                      # BR-T #14: the queue's, not an error
        except SystemExit as e:
            # MF §B #19: the library's our_exit. Caught only at _bridge_loop,
            # it left the row in_progress and skipped the batch. Here it is a
            # send failure like any other: counted, retried, dead-lettered named.
            bump_stat(self, 'library_exit_caught')
            logger.error(f"Forward to {dest_name} FAILED: library exit "
                         f"(SystemExit: {e}) — counted, the queue retries")
            with self._stats_lock:
                self.stats['errors'] += 1
            return False
        except Exception as e:
            logger.error(f"Failed to forward to {dest_name}: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            return False

    # ========================================
    # Callbacks
    # ========================================

    def _notify_message(self, msg: BridgedMeshMessage):
        """Notify message callbacks."""
        for callback in list(self._message_callbacks):
            try:
                callback(msg)
            except Exception as e:
                logger.error(f"Message callback error: {e}")

    def _notify_status(self, status: str):
        """Notify status callbacks."""
        status_data = self.get_status()
        for callback in list(self._status_callbacks):
            try:
                callback(status, status_data)
            except Exception as e:
                logger.error(f"Status callback error: {e}")


def create_mesh_bridge(config: Optional[GatewayConfig] = None) -> MeshtasticPresetBridge:
    """
    Create a Meshtastic preset bridge instance.

    Args:
        config: Optional gateway config, loads from file if not provided

    Returns:
        Configured bridge instance (not started)
    """
    return MeshtasticPresetBridge(config)
