"""Send / persistent-queue surface for RNSMeshtasticBridge.

Part of the 2026-06-09 rns_bridge.py split (MeshForge parity — the same
mixin seams land in the sister repo). RNSMeshtasticBridge is the only
consumer; methods keep their pre-extraction signatures so attribute
access via the bridge instance/class is unchanged.

Host class must provide:
- self.config (GatewayConfig), self.stats, self._stats_lock
- self._connected_rns, self._stop_event
- self._lxmf_source / self._lxmf_router (RNSConnectionMixin)
- self._mesh_handler / self._meshcore_handler (or None)
- self._persistent_queue (PersistentMessageQueue or None)
- self.health (BridgeHealthMonitor) — meshtastic_path_available()
- self.delivery_tracker (DeliveryTracker)
- self.can_send_to / record_send_success / record_send_failure
  (BridgeHealthMixin)
- self._maybe_emit_ack_for_msgid (BridgeAckMixin)
- self.node_tracker (UnifiedNodeTracker)
"""

import logging
from typing import Dict, Optional

from gateway.message_queue_models import (
    AGE_SANITY_CAP_S,
    M2R_MAX_AGE_S,
    SendDeferred,
)
from gateway.delivery_legs import payload_source, synth_id
from utils.boundary_timing import call_boundary
from utils.safe_import import safe_import
from utils.tx_guard import assert_rns_tx_allowed

from .base_handler import chunk_for_mesh
from .bridge_health import SubsystemState

logger = logging.getLogger(__name__)


class _LazyDeliveryCounters:
    """Deferred delivery_counters import — see the twin in
    message_queue.py: a module-level parent-package import from a
    gateway submodule deadlocked the daemon's threaded startup
    (cross-thread _ModuleLock cycle, 2026-06-06). First attribute
    access imports and replaces this proxy."""

    def __getattr__(self, name):
        from gateway import delivery_counters as mod
        globals()["_dc"] = mod
        return getattr(mod, name)


_dc = _LazyDeliveryCounters()

# Persistent-queue priority enum — mirrors the rns_bridge safe_import.
# Tests exercising the moved enqueue/requeue methods patch
# gateway.bridge_send_mixin.MessagePriority (split patch-target repoint).
MessagePriority, HAS_MESSAGE_QUEUE = safe_import(
    '.message_queue', 'MessagePriority', package=__package__
)


def _coerce_metadata_for_json(obj):
    """Recursively decode bytes inside dict/list payloads.

    Issue #66: PersistentMessageQueue.enqueue computes a dedup hash via
    json.dumps; raw bytes (LXMF title fields, sender_key, etc.) raise and
    drop the message silently. errors='replace' so corrupt input never
    crashes the requeue path.
    """
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {k: _coerce_metadata_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_coerce_metadata_for_json(v) for v in obj]
    return obj


class BridgeSendMixin:
    """Mixin: direct sends, queue dispatch, and requeue-on-failure."""

    def _meshtastic_egress_configured(self) -> bool:
        """Whether a remote Meshtastic egress is configured AND usable.

        Single reading of the three fields that make ``send_to_meshtastic``'s
        radio-less branch live. Kept private and derived so no caller
        re-spells the test (honest_failure_modes #5: two consumers of one
        concept, independently coded, WILL drift — which is exactly how the
        RNS→Mesh leg went dead, see below).
        """
        eg = getattr(self.config, 'meshtastic_egress', None)
        return bool(eg and getattr(eg, 'enabled', False)
                    and getattr(eg, 'host', ''))

    def meshtastic_path_available(self) -> bool:
        """Whether ``send_to_meshtastic`` has ANY route to the Meshtastic net.

        THE predicate every Meshtastic gate must ask. It exists because two
        gates asked it independently and disagreed for five days (2026-09-17):
        ``meshcore_bridge_mixin`` tested ``egress_on or state not in (...)``
        and kept working, while the RNS→Mesh worker in ``rns_bridge`` tested
        only the subsystem state. On a radio-less gateway that state is
        ``DISABLED`` **by design** — it is the very condition
        ``meshtastic_egress`` exists to cover — so the RNS worker took its
        degraded branch on every message and ``_process_rns_to_mesh``, the
        only path that reaches the egress, never executed at all. Every
        RNS→Meshtastic message was silently discarded.

        Answer the question here, once, or the third consumer drifts too.
        """
        if self._meshtastic_egress_configured():
            return True
        state = self.health.get_subsystem_state("meshtastic")
        return state not in (SubsystemState.DISCONNECTED,
                             SubsystemState.DISABLED)

    def _queue_send_meshtastic_egress(self, payload: Dict) -> bool:
        """Persistent-queue sender for a radio-less gateway's remote egress.

        Registered under ``"meshtastic"`` when there is no local
        ``_mesh_handler`` but an egress is configured. Without it, a queued
        Meshtastic message is REJECTED at enqueue (Issue #67's no-sender
        drop) and discarded rather than retried — the second half of the
        2026-09-17 defect above.

        Channel: deliberately NOT forwarded. ``send_to_meshtastic``'s egress
        branch uses ``meshtastic_egress.channel_index`` (the PEER gateway's
        slot) and ignores the local-radio channel arg, so a replay lands on
        the same channel the original direct send would have used. Passing
        ``payload['channel']`` here would put a retry on a different channel
        than the message it retries — and that replay path is where Issue
        #37's privacy class lived.

        ``destination`` is forwarded for symmetry with the local-handler
        sender, but note the egress branch cannot honour it: ``send_text_direct``
        takes no destination, so an egressed DM goes out as a channel
        broadcast. That is pre-existing behaviour of the direct path, not
        something the queue introduces — the queue matching it is the point.
        """
        message = payload.get('message') or payload.get('content') or ''
        if not message:
            return False
        destination = payload.get('destination')

        # Chunk rather than truncate. The local-handler sender path runs
        # _truncate_if_needed; an un-chunked requeue (_requeue_failed_message
        # persists the whole original) would therefore lose every line past
        # the cap. chunk_for_mesh is the same helper the direct RNS→Mesh path
        # uses, so a retried message keeps the content the first attempt had.
        chunks = chunk_for_mesh(message)
        if not chunks:
            return False
        # Send EVERY chunk, then judge — deliberately not a short-circuiting
        # all(), which would abandon chunks 2..n after one transient failure.
        # Mirrors the direct RNS→Mesh path in rns_bridge, which builds its
        # failed_chunks list the same way. All-or-nothing on the return so a
        # partial send is never recorded as a delivery.
        # BIC-2: the row's leg; the queue's mark_delivered owns its SENT.
        src = payload_source(payload)
        results = [self.send_to_meshtastic(chunk, destination,
                                           source=src, record_sent=False)
                   for chunk in chunks]
        return all(results)

    def send_to_meshtastic(self, message: str, destination: str = None, channel: int = 0,
                           *, source: Optional[str] = None,
                           record_sent: bool = True) -> bool:
        """Send a message to Meshtastic network.

        Prefers a local Meshtastic handler. When there is no local radio
        but ``meshtastic_egress`` is configured, falls back to a stateless
        remote HTTP egress (send_text_direct → peer meshtasticd's
        /api/v1/toradio, e.g. moc:9443). TX-only — never reads fromradio.
        The egress's own channel_index is used (the peer gateway's channel,
        e.g. meshforge=2), not the local-radio channel arg.

        ``source`` names the BIC-2 leg ``source>meshtastic`` (omitted:
        ``?>meshtastic``); ``record_sent`` is False from a persistent-queue
        sender, whose row's SENT the queue already owns.
        """
        if self._mesh_handler:
            return self._mesh_handler.send_text(message, destination, channel,
                                                source=source,
                                                record_sent=record_sent)

        eg = getattr(self.config, 'meshtastic_egress', None)
        if eg and getattr(eg, 'enabled', False) and eg.host:
            try:
                from .meshtastic_protobuf_client import send_text_direct
                ok = send_text_direct(
                    message,
                    host=eg.host,
                    port=int(eg.port),
                    tls=bool(eg.tls),
                    channel_index=int(eg.channel_index),
                    want_ack=bool(getattr(eg, 'want_ack', True)),
                )
                if ok and record_sent:
                    self._record_direct_sent("meshtastic", "mesh", source)
                return ok
            except Exception as e:
                logger.warning("Meshtastic remote egress failed: %s", e)
                with self._stats_lock:
                    self.stats['errors'] += 1
                return False

        logger.warning("Meshtastic handler not initialized")
        return False

    def _record_direct_sent(self, protocol: str, prefix: str,
                            source: Optional[str],
                            msg_id: Optional[str] = None) -> None:
        """SENT for a successful direct send on the BIC-2 leg
        ``source>protocol`` (slice 2b, MeshForge twin of slice 1's
        send_to_rns SENT + f5c6a26d). Fenced: the message is already out,
        and a bookkeeping error reported as a send failure would requeue it
        — a duplicate on the air. The swallow leaves a witness (hfm #9)."""
        try:
            _dc.record(_dc.DeliveryState.SENT,
                       msg_id=msg_id or synth_id(prefix),
                       protocol=protocol, source=source)
        except Exception as e:  # noqa: BLE001
            logger.warning("delivery counter SENT not recorded: %s", e)
            with self._stats_lock:
                self.stats['delivery_record_errors'] = (
                    self.stats.get('delivery_record_errors', 0) + 1)

    def send_to_rns(self, message: str, destination_hash: bytes = None,
                    *, source: Optional[str] = None) -> bool:
        """Send a message to RNS network via LXMF.

        ``source`` is the transport the message came from; it names the
        BIC-2 delivery leg, and the receipt's CONFIRMED / DROPPED lands on
        the same leg as the SENT. Omitted, the send counts under ``?>rns``.
        """
        if not self._connected_rns:
            logger.warning("Not connected to RNS")
            return False

        if self._lxmf_source is None:
            logger.warning("LXMF source not initialized (partial RNS init)")
            return False

        try:
            import RNS
            import LXMF

            if destination_hash:
                # Direct message
                hash_short = destination_hash.hex()[:8]
                # MF Issue #74 port: gate on the per-destination
                # circuit BEFORE any RNS RPC. The breaker was
                # write-only — can_send_to/record_send_* had zero
                # callers, so an open circuit never blocked a send and
                # organic failures never fed the threshold-OPEN.
                if not self.can_send_to(hash_short):
                    logger.warning(
                        f"Send to {hash_short} blocked: circuit open "
                        f"(recent failures; retry after recovery window)"
                    )
                    return False
                if not call_boundary("rnsd.has_path",
                                     RNS.Transport.has_path, destination_hash,
                                     target=hash_short):
                    call_boundary("rnsd.request_path",
                                  RNS.Transport.request_path, destination_hash,
                                  target=hash_short)
                    # Wait briefly for path (interruptible on shutdown)
                    for _ in range(50):
                        if RNS.Transport.has_path(destination_hash):
                            break
                        if self._stop_event.wait(0.1):
                            break

                if not RNS.Transport.has_path(destination_hash):
                    logger.warning("No path to destination")
                    # Per-destination failure: feeds the threshold-based
                    # OPEN transition so repeated no-path sends stop
                    # hammering path requests (MF #74).
                    self.record_send_failure(hash_short, "no path")
                    return False

                dest_identity = call_boundary("rnsd.identity_recall",
                                              RNS.Identity.recall, destination_hash,
                                              target=hash_short)
                destination = RNS.Destination(
                    dest_identity,
                    RNS.Destination.OUT,
                    RNS.Destination.SINGLE,
                    "lxmf",
                    "delivery"
                )
            else:
                # Broadcast not directly supported in LXMF
                logger.info(
                    "Broadcast to RNS dropped: set rns.default_lxmf_destination "
                    "in gateway config to route broadcasts to a specific LXMF peer"
                )
                return False

            lxm = LXMF.LXMessage(
                destination,
                self._lxmf_source,
                message,
                "MeshAnchor Gateway"
            )

            # Track delivery confirmation
            msg_id = synth_id("lxmf")
            self.delivery_tracker.track_message(
                msg_id, destination_hash, message[:50]
            )

            # Register LXMF delivery/failure callbacks
            #
            # Issue #66: on_delivered + on_failed also call
            # _maybe_emit_ack_for_msgid so messages with ack_required=True
            # (and a registered pending-ack record in the queue) get a
            # synthetic ACK CanonicalMessage routed back to the origin
            # protocol. Idempotent — mark_acked() returns None on the
            # second call to prevent double-emission.
            def on_delivered(receipt):
                self.delivery_tracker.confirm_delivery(msg_id)
                # MF Issue #74 port: durable CONFIRMED — the receiver
                # proved delivery. Feeds the confirmation ring the
                # delivery_confirmation_stall check judges.
                _dc.record(
                    _dc.DeliveryState.CONFIRMED,
                    msg_id=msg_id, protocol="rns", source=source,
                )
                self._maybe_emit_ack_for_msgid(msg_id, kind='delivered')

            def on_failed(receipt):
                reason = "delivery_failed"
                if hasattr(receipt, 'failure_reason'):
                    reason = str(receipt.failure_reason)
                self.delivery_tracker.confirm_failure(msg_id, reason)
                _dc.record(
                    _dc.DeliveryState.DROPPED,
                    msg_id=msg_id, protocol="rns", source=source,
                    drop_reason=_dc.DropReason.RNS_DELIVERY_FAILED,
                    note=reason[:80],
                )
                self._maybe_emit_ack_for_msgid(msg_id, kind='failed')

            try:
                lxm.register_delivery_callback(on_delivered)
                lxm.register_failed_callback(on_failed)
            except (AttributeError, TypeError):
                # LXMF version may not support callbacks
                logger.debug("LXMF callbacks not available, skipping delivery tracking")

            assert_rns_tx_allowed(kind="lxmf_outbound",
                                  detail=f"bridge send -> {hash_short}")
            call_boundary("rnsd.handle_outbound",
                          self._lxmf_router.handle_outbound, lxm,
                          target=hash_short)
            self.record_send_success(hash_short)
            # BIC-2: the direct path's SENT. Only the queue path recorded
            # one, so a direct send's CONFIRMED had no SENT to answer.
            self._record_direct_sent("rns", "lxmf", source, msg_id=msg_id)
            return True

        except Exception as e:
            logger.error(f"Failed to send to RNS: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            if destination_hash:
                self.record_send_failure(
                    destination_hash.hex()[:8], str(e)
                )
            return False

    def _register_rns_queue_senders(self, queue) -> None:
        """Register the two M→R queue senders (MF lead). Both are gated on
        RNS being connected, so rows wait out an outage instead of burning
        the queue's 3-try budget on it (F4 review R2). "rns_xform" is F4's
        whole-message replay through _process_mesh_to_rns."""
        ready = lambda: bool(self._connected_rns)  # noqa: E731
        # ROADMAP 6f: deferred or gated M→R rows expire after M2R_MAX_AGE_S.
        # on_terminal: each row's final outcome resolves the message it
        # carries — delivered / dropped counted exactly once (_m2r_accounting).
        queue.register_sender("rns", self._queue_send_rns, ready_fn=ready,
                              max_age_s=M2R_MAX_AGE_S,
                              on_terminal=self._m2r_on_terminal)
        queue.register_sender("rns_xform", self._dispatch_rns_xform_spill,
                              ready_fn=ready, max_age_s=M2R_MAX_AGE_S,
                              on_terminal=self._m2r_on_xform_terminal)

    @staticmethod
    def _m2r_defer_delay(payload: Dict) -> float:
        """No-path backoff (ROADMAP 6f): 15 s doubling per deferral, 5 min cap."""
        try:
            n = max(0, int(payload.get('_deferrals', 0) or 0))
        except (TypeError, ValueError):
            n = 0
        return float(min(15 * (2 ** min(n, 10)), 300))

    def _circuit_recovery_s(self) -> float:
        value = getattr(self._circuit_breaker, 'recovery_timeout', None)
        if isinstance(value, (int, float)) and not isinstance(value, bool) \
                and value > 0:
            return float(value)
        return 60.0

    @staticmethod
    def _queued_seconds(payload: Dict) -> Optional[int]:
        """How long this row waited (from the queue's _queue_created_at), or
        None when unknowable — a clock jump is never reported as a wait."""
        from datetime import datetime
        try:
            created = datetime.fromisoformat(payload['_queue_created_at'])
        except (KeyError, TypeError, ValueError):
            return None
        waited = (datetime.now() - created).total_seconds()
        if waited < 0 or waited > AGE_SANITY_CAP_S:
            return None
        return int(waited)

    def _queue_send_rns(self, payload: Dict) -> bool:
        """Send handler for persistent queue - RNS destination."""
        message = payload.get('message', '')
        destination_hash = payload.get('destination_hash')

        if not self._connected_rns:
            return False

        # MF Issue #74 port: circuit gate BEFORE the try block — the
        # inner except would swallow a raise into `return False`, which
        # the queue classifies as an unknown error (one short retry,
        # then dead-letter). Raising with a retriable-pattern message
        # ("temporarily unavailable") makes RetryPolicy back off and
        # retry after the circuit's recovery window instead.
        if destination_hash:
            _gate_key = (
                destination_hash.hex()[:8]
                if isinstance(destination_hash, bytes)
                else str(destination_hash).lower()[:8]
            )
            if not self.can_send_to(_gate_key):
                # ROADMAP 6f (MF lead): defer to the circuit's recovery
                # window — not an attempt. The #74 retriable raise assumed a
                # RetryPolicy the bridge queue never had.
                raise SendDeferred(self._circuit_recovery_s(),
                                   f"circuit open for {_gate_key}")

        try:
            import RNS
            import LXMF

            if not destination_hash:
                return False

            if isinstance(destination_hash, str):
                destination_hash = bytes.fromhex(destination_hash)

            hash_short = destination_hash.hex()[:8]
            if not call_boundary("rnsd.has_path",
                                 RNS.Transport.has_path, destination_hash,
                                 target=hash_short):
                call_boundary("rnsd.request_path",
                              RNS.Transport.request_path, destination_hash,
                              target=hash_short)
                # ROADMAP 6f: no in-worker busy-wait — the path request is
                # out; come back later.
                self.record_send_failure(hash_short, "no path")
                raise SendDeferred(self._m2r_defer_delay(payload), "no path")

            dest_identity = call_boundary("rnsd.identity_recall",
                                          RNS.Identity.recall, destination_hash,
                                          target=hash_short)
            destination = RNS.Destination(
                dest_identity, RNS.Destination.OUT,
                RNS.Destination.SINGLE, "lxmf", "delivery"
            )

            queued_s = self._queued_seconds(payload)
            if queued_s is not None:
                # Late delivery is still delivery; say how late (6f, Q3).
                lxm = LXMF.LXMessage(destination, self._lxmf_source, message,
                                     "MeshAnchor Gateway",
                                     fields={'meshforge_queued_s': queued_s})
            else:
                lxm = LXMF.LXMessage(destination, self._lxmf_source, message,
                                     "MeshAnchor Gateway")

            # MF Issue #74 port (Fork C syn/ack): pin the LXMF delivery
            # callbacks to the queue row's id (injected at dispatch by
            # process_once) so history_for(msg_id) joins QUEUED (enqueue)
            # → SENT (mark_delivered) → CONFIRMED (delivery proof).
            # Without this, queue-path sends record SENT but never
            # CONFIRMED — biasing the confirmation ring the
            # delivery_confirmation_stall check judges.
            msg_id = (
                payload.get('_queue_msg_id')
                or synth_id("lxmf")
            )

            # BIC-2: the receipt confirms on the queue row's leg.
            _src = payload_source(payload)

            def on_delivered(receipt, _mid=msg_id):
                self.delivery_tracker.confirm_delivery(_mid)
                _dc.record(
                    _dc.DeliveryState.CONFIRMED,
                    msg_id=_mid, protocol="rns", source=_src,
                )
                self._maybe_emit_ack_for_msgid(_mid, kind='delivered')

            def on_failed(receipt, _mid=msg_id):
                reason = "delivery_failed"
                if hasattr(receipt, 'failure_reason'):
                    reason = str(receipt.failure_reason)
                self.delivery_tracker.confirm_failure(_mid, reason)
                _dc.record(
                    _dc.DeliveryState.DROPPED,
                    msg_id=_mid, protocol="rns", source=_src,
                    drop_reason=_dc.DropReason.RNS_DELIVERY_FAILED,
                    note=reason[:80],
                )
                self._maybe_emit_ack_for_msgid(_mid, kind='failed')

            try:
                lxm.register_delivery_callback(on_delivered)
                lxm.register_failed_callback(on_failed)
            except (AttributeError, TypeError):
                logger.debug(
                    "LXMF callbacks not available, skipping delivery tracking"
                )

            assert_rns_tx_allowed(kind="lxmf_outbound",
                                  detail=f"bridge send -> {hash_short}")
            call_boundary("rnsd.handle_outbound",
                          self._lxmf_router.handle_outbound, lxm,
                          target=hash_short)
            self.record_send_success(hash_short)
            return True

        except SendDeferred:
            raise
        except Exception as e:
            logger.error(f"Queue send to RNS failed: {e}")
            if destination_hash:
                _fail_key = (
                    destination_hash.hex()[:8]
                    if isinstance(destination_hash, bytes)
                    else str(destination_hash).lower()[:8]
                )
                self.record_send_failure(_fail_key, str(e))
            return False

    def enqueue_message(self, message: str, destination: str, dest_type: str = "meshtastic",
                        priority: str = "normal",
                        ack_required: bool = False,
                        ack_origin_network: Optional[str] = None,
                        ack_origin_address: Optional[str] = None,
                        ack_timeout_seconds: int = 300,
                        **kwargs) -> Optional[str]:
        """
        Enqueue a message for reliable delivery.

        Args:
            message: Message content
            destination: Destination ID/hash
            dest_type: "meshtastic" or "rns"
            priority: "low", "normal", "high", or "urgent"
            ack_required: Issue #66 — opt in to application-layer ack.
                When True (and both origin fields are set), a successful
                delivery callback emits a synthetic ACK CanonicalMessage
                back to (ack_origin_network, ack_origin_address). When
                the destination doesn't ack within ack_timeout_seconds
                the periodic sweep emits a TIMEOUT ACK instead.
            ack_origin_network: where to route the synthetic ACK back to
                ("meshtastic" / "meshcore" / "rns"). Required when
                ack_required=True.
            ack_origin_address: address on ack_origin_network. Required
                when ack_required=True.
            ack_timeout_seconds: how long before emitting a TIMEOUT ACK.
                Defaults to 5 minutes (PersistentMessageQueue default).
            **kwargs: Additional parameters (channel, etc.)

        Returns:
            Message ID if enqueued, None if queue unavailable
        """
        # BIC-2: a caller may name its leg; popped so it never rides the
        # payload as a free-form key. The direct fall-back names it too.
        leg_source = kwargs.pop('source', None)
        if not self._persistent_queue:
            # Fall back to direct send. ack_required is silently ignored
            # here — direct sends bypass the queue's pending-ack table,
            # so there's nothing to register.
            if dest_type == "meshtastic":
                return "direct" if self.send_to_meshtastic(
                    message, destination, kwargs.get('channel', 0),
                    source=leg_source) else None
            else:
                dest_hash = kwargs.get('destination_hash')
                if isinstance(dest_hash, str):
                    dest_hash = bytes.fromhex(dest_hash)
                return "direct" if self.send_to_rns(
                    message, dest_hash, source=leg_source) else None

        # Map priority string to enum
        priority_map = {
            "low": MessagePriority.LOW,
            "normal": MessagePriority.NORMAL,
            "high": MessagePriority.HIGH,
            "urgent": MessagePriority.URGENT,
        }
        msg_priority = priority_map.get(priority, MessagePriority.NORMAL)

        payload = {
            'message': message,
            'destination': destination,
            **kwargs
        }

        msg_id = self._persistent_queue.enqueue(
            payload=payload,
            destination=dest_type,
            priority=msg_priority,
            source=leg_source,
        )

        # Issue #66: register the pending-ack record so the LXMF
        # delivery callback / overdue sweep can synthesize back to
        # origin. Both origin fields must be present — silent skip
        # otherwise keeps the contract simple.
        if (msg_id
                and ack_required
                and ack_origin_network
                and ack_origin_address):
            try:
                self._persistent_queue.register_pending_ack(
                    msg_id,
                    origin_network=ack_origin_network,
                    origin_address=ack_origin_address,
                    timeout_seconds=ack_timeout_seconds,
                )
            except Exception as e:
                logger.warning(
                    f"register_pending_ack failed for {msg_id[:8]}: {e}"
                )

        return msg_id

    def get_queue_stats(self) -> Dict:
        """Get persistent queue statistics."""
        if self._persistent_queue:
            return self._persistent_queue.get_stats()
        return {}

    def _drain_persistent_queue(self) -> None:
        """Process pending messages from the persistent queue.

        Called periodically from _bridge_loop when subsystems are healthy.
        Only drains messages destined for currently-connected subsystems.
        """
        if not self._persistent_queue:
            return
        try:
            self._persistent_queue.process_once(batch_size=5)
        except Exception as e:
            logger.warning(f"Persistent queue drain error: {e}")

    def _get_rns_destination(self, meshtastic_id: str) -> bytes:
        """Look up RNS destination hash for a Meshtastic node ID"""
        # Check node tracker for known mappings
        if hasattr(self, 'node_tracker') and self.node_tracker:
            node = self.node_tracker.get_node_by_mesh_id(meshtastic_id)
            if node and hasattr(node, 'rns_hash') and node.rns_hash:
                return node.rns_hash
        return None

    @staticmethod
    def _leg_source(msg) -> Optional[str]:
        """BIC-2: the transport a message came in on (twin of MeshForge's
        ``_rns_bridge_xform._leg_source``). source_network is a routing
        input and reads "meshtastic" for MQTT ingress, so the leg reads the
        origin: a replayed spill carries it in metadata, a live MQTT message
        as origin=MQTT (enum or plain string). A replay that carries no leg
        (a row queued before BIC-2) is UNKNOWN: its rebuilt source_network
        is the hard-coded "meshtastic", never a measurement."""
        meta = getattr(msg, 'metadata', None) or {}
        leg = meta.get('m2r_leg_source')
        if leg:
            return leg
        if meta.get('m2r_replay'):
            return None
        origin = getattr(msg, 'origin', None)
        if getattr(origin, 'value', origin) == 'mqtt':
            return 'mqtt'
        return getattr(msg, 'source_network', None)

    def _requeue_failed_message(self, msg, destination: str,
                                *, channel_override: Optional[int] = None,
                                rns_destinations=None,
                                rns_message: Optional[str] = None,
                                origin: Optional[str] = None) -> bool:
        """Persist a failed message to the persistent queue for later retry.

        Args:
            msg: The message that failed to send (BridgedMessage or CanonicalMessage).
            destination: Target network ("meshtastic", "rns", or "meshcore").
            channel_override: When set, this slot is written to the
                payload's top-level ``channel`` field — overriding any
                ``metadata['channel']`` lift. Cross-protocol bridge
                cargo passes this (resolved via
                ``_resolve_bridge_target_channel``) so the replay path
                doesn't preserve the SOURCE protocol's channel index.
                Without this override, Issue #37's privacy class was
                still live via the replay path even after the resolver
                fix — bug discovered live 2026-05-20.
            rns_destinations: for "rns", the LXMF hashes the send already
                resolved — one "rns" row each, in the shape _queue_send_rns
                reads (bridge matrix F4, MF lead). Without them an "rns"
                message replays whole through _process_mesh_to_rns
                ("rns_xform"), so F14 + the broadcast fan-out decide again.
            rns_message: the exact text the live send carried (with the
                ``[Mesh:xxxx]`` prefix) for those rows.
            origin: M→R outcome accounting (_m2r_accounting) — the rows
                carry it and the message is tracked PENDING on as many rows
                as were actually persisted.

        Returns:
            True if message was successfully persisted, False otherwise.
        """
        if not self._persistent_queue:
            return False

        try:
            # Handle both BridgedMessage (source_id) and CanonicalMessage (source_address)
            source_id = getattr(msg, 'source_id', None) or getattr(msg, 'source_address', '')
            dest_id = getattr(msg, 'destination_id', None) or getattr(msg, 'destination_address', '')
            # BridgedMessage.__post_init__ does NOT centralize bytes→str on
            # MeshAnchor; CanonicalMessage may arrive with bytes content, and
            # LXMF metadata frequently carries bytes title/sender_key. Coerce
            # both — json.dumps inside enqueue's dedup-hash step otherwise
            # raises and drops the message (Issue #66 mirror gap).
            content = msg.content
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            elif not isinstance(content, str):
                content = ""
            metadata = _coerce_metadata_for_json(msg.metadata or {})
            if destination == "rns":
                return self._requeue_failed_rns(
                    msg, source_id, dest_id, content, metadata,
                    rns_destinations, rns_message, origin)
            # Lift channel into the top-level payload — channel-0 Public
            # leak follow-up (2026-05-20). The persistent queue's MeshCore
            # sender (meshcore_handler.queue_send → _process_outbound)
            # reads ``msg.get('channel')`` from the outer dict; without
            # this lift, a requeued message would have channel=None and
            # `_resolve_channel` would default it to 0 (Public), leaking
            # private-channel cargo on replay after a disconnect window.
            payload = {
                'message': content,
                'source_id': source_id,
                'destination_id': dest_id or "",
                'metadata': metadata,
            }
            if destination == "meshcore":
                # Issue #37 class (2026-10-24): a MeshCore row carries ONLY
                # the slot the caller resolved, verbatim — never
                # metadata.channel (the SOURCE protocol's index; Meshtastic's
                # primary is 0 = MeshCore Public). The queue's validate_fn
                # (validate_queue_row) is the ONE gate that refuses a missing
                # or non-slot value: one counter, one DROPPED invalid_payload
                # (review of 39f566c1, #3/#9). enqueue returns None → False.
                if channel_override is not None:
                    payload['channel'] = channel_override
            elif channel_override is not None:
                # Caller already resolved the destination slot. Write it
                # verbatim — do NOT fall through to metadata.channel.
                try:
                    payload['channel'] = int(channel_override)
                except (TypeError, ValueError):
                    pass
            else:
                raw_channel = metadata.get('channel') if isinstance(metadata, dict) else None
                if raw_channel is not None and raw_channel != '':
                    try:
                        payload['channel'] = int(raw_channel)
                    except (TypeError, ValueError):
                        pass
            msg_id = self._persistent_queue.enqueue(
                payload=payload,
                destination=destination,
                priority=MessagePriority.HIGH,
                source=self._leg_source(msg),
            )
            if msg_id is None:
                # Issue #67: enqueue() drops when no sender is registered
                # for the destination (or when deduped, or queue-full).
                # Don't lie about a successful requeue — health reporting
                # uses this return value.
                return False
            logger.debug(f"Failed message re-queued to persistent storage ({destination})")
            return True
        except Exception as e:
            logger.error(f"Failed to persist message for retry: {e}")
            return False

    def _requeue_failed_rns(self, msg, source_id, dest_id, content, metadata,
                            rns_destinations, rns_message,
                            origin: Optional[str] = None) -> bool:
        """F4 body of _requeue_failed_message for destination "rns"."""
        # ROADMAP 6f: NORMAL so a long single-peer outage sheds its OWN
        # oldest rows under pressure, never fresh traffic (Q16); a row
        # continuing an older wait keeps its first enqueue time (Q17).
        from datetime import datetime
        try:
            first_enqueued = datetime.fromisoformat(
                (metadata or {}).get('m2r_first_enqueued_at'))
        except (TypeError, ValueError):
            first_enqueued = None
        persisted = 0
        if origin:  # BEFORE the first row can reach a terminal (MF review R1)
            legs = len(rns_destinations) if rns_destinations else 1
            self._m2r_track(origin, legs)
            if rns_destinations is None:
                metadata = {**(metadata or {}), 'm2r_origin': origin}
        try:
            if rns_destinations is not None:
                # Resolved legs, possibly none — an empty list has nothing to
                # retry at, and replaying it would only loop (review R3).
                for dest_hash in rns_destinations:
                    dest_hex = dest_hash.hex()
                    try:
                        if self._persistent_queue.enqueue(
                            payload={
                                'message': rns_message if rns_message is not None
                                else content,
                                # 'destination' keys the queue's dedup hash —
                                # without it every leg of a fan-out collapses.
                                'destination': dest_hex,
                                'destination_hash': dest_hex,
                                'source_id': source_id,
                                **({'m2r_origin': origin} if origin else {}),
                            },
                            destination="rns",
                            priority=MessagePriority.NORMAL,
                            created_at=first_enqueued,
                            source=self._leg_source(msg),
                        ):
                            persisted += 1
                    except Exception as e:  # noqa: BLE001
                        # A raising enqueue (sqlite locked / disk I/O) is THIS
                        # leg not persisted; written legs keep it pending.
                        with self._stats_lock:
                            self.stats['m2r_requeue_enqueue_errors'] = (
                                self.stats.get('m2r_requeue_enqueue_errors', 0) + 1)
                        logger.error(f"Mesh→RNS requeue: enqueue for "
                                     f"{dest_hex[:8]} raised: {e}")
            elif self._persistent_queue.enqueue(
                payload={
                    'source_id': source_id,
                    'destination_id': dest_id or "",
                    'content': content,
                    'title': getattr(msg, 'title', None),
                    'is_broadcast': bool(getattr(msg, 'is_broadcast', False)),
                    'content_id': getattr(msg, 'content_id', '') or '',
                    'metadata': metadata,
                },
                destination="rns_xform",
                priority=MessagePriority.NORMAL,
                created_at=first_enqueued,
                source=self._leg_source(msg),
            ):
                persisted += 1
        finally:
            if origin:
                self._m2r_settle(origin, persisted)
        if persisted:
            logger.debug(f"Failed Mesh→RNS message re-queued ({persisted} row(s))")
        return persisted > 0

    def _dispatch_rns_xform_spill(self, payload: Dict) -> bool:
        """Persistent-queue sender for a whole-message M→R replay (F4).

        Rebuilds the BridgedMessage and runs _process_mesh_to_rns, so the
        retry gets the same F14 refusal / fan-out / accounting as a live
        message. Returns False while RNS is disconnected so the row waits
        out the queue's backoff instead of replaying into a dead pipeline.
        """
        if not self._connected_rns:
            return False
        from . import rns_bridge as _rns_bridge_module
        BridgedMessage = _rns_bridge_module.BridgedMessage
        try:
            metadata = dict(payload.get('metadata') or {})
            # A replay that raises again must not requeue itself forever.
            metadata['m2r_replay'] = True
            # BIC-2: the real origin for the leg; source_network below is a
            # routing input and stays as it was.
            leg = payload_source(payload)
            if leg:
                metadata['m2r_leg_source'] = leg
            # Q17: a failed replay's retry rows keep the FIRST enqueue time.
            if payload.get('_queue_created_at'):
                metadata.setdefault('m2r_first_enqueued_at',
                                    payload['_queue_created_at'])
            msg = BridgedMessage(
                source_network="meshtastic",
                source_id=payload.get('source_id', '') or '',
                destination_id=payload.get('destination_id') or None,
                content=payload.get('content', '') or '',
                title=payload.get('title'),
                is_broadcast=bool(payload.get('is_broadcast', False)),
                content_id=payload.get('content_id', '') or '',
                metadata=metadata,
            )
        except Exception as e:
            logger.error(f"rns_xform replay dispatch failed: {e}")
            return False
        try:
            self._process_mesh_to_rns(msg)
        except Exception as e:  # noqa: BLE001
            # The replay already claimed its origin: a retry of this row would
            # count the message a second time. Done — with a witness.
            with self._stats_lock:
                self.stats['m2r_replay_raised'] = (
                    self.stats.get('m2r_replay_raised', 0) + 1)
            logger.error(f"rns_xform replay raised (not retried): {e}")
        return True

    def _requeue_failed_chunks(self, chunks, target: str = "meshtastic", *,
                               source: Optional[str]) -> bool:
        """Persist already-chunked, byte-bounded RNS→Mesh content for retry.

        Ported from MeshForge. Used by the direct-send path on PARTIAL
        failure: each failed chunk is enqueued as its own ``message``-shaped
        item (already ≤ the byte cap) so the retry ships bounded packets,
        rather than re-queuing the whole un-chunked original — which the
        retry's ``_truncate_if_needed`` would truncate (data loss) and which
        would also re-send the chunks that already went out. Returns True if
        at least one chunk was persisted.
        """
        if not self._persistent_queue or not chunks:
            return False
        requeued = 0
        for chunk in chunks:
            try:
                if self._persistent_queue.enqueue(
                    payload={
                        'message': chunk,
                        'channel': self.config.meshtastic.channel,
                        'source_id': '',
                    },
                    destination=target,
                    priority=MessagePriority.HIGH,
                    source=source,   # BIC-2: required — no default origin
                ):
                    requeued += 1
            except Exception as e:
                logger.error(f"Failed to persist RNS→Mesh chunk for retry: {e}")
        return requeued > 0

    def _dual_path_dedup_on(self) -> bool:
        """Strict read of rns.dual_path_dedup_enabled (default False).

        ``is True`` deliberately (MeshForge gate discipline): MagicMock test
        configs and malformed gateway.json values are truthy-but-not-True
        and must read as OFF, preserving the legacy always-deliver behavior
        everywhere the flag wasn't explicitly enabled.
        """
        rns_cfg = getattr(self.config, 'rns', None)
        return getattr(rns_cfg, 'dual_path_dedup_enabled', False) is True

    def _dual_path_dedup_window(self) -> float:
        rns_cfg = getattr(self.config, 'rns', None)
        try:
            return float(getattr(rns_cfg, 'dual_path_dedup_window_sec', 60))
        except (TypeError, ValueError):
            return 60.0
