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
- self.delivery_tracker (DeliveryTracker)
- self.can_send_to / record_send_success / record_send_failure
  (BridgeHealthMixin)
- self._maybe_emit_ack_for_msgid (BridgeAckMixin)
- self.node_tracker (UnifiedNodeTracker)
"""

import logging
import time
from typing import Dict, Optional

from utils.boundary_timing import call_boundary
from utils.safe_import import safe_import

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

    def send_to_meshtastic(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """Send a message to Meshtastic network.

        Prefers a local Meshtastic handler. When there is no local radio
        but ``meshtastic_egress`` is configured, falls back to a stateless
        remote HTTP egress (send_text_direct → peer meshtasticd's
        /api/v1/toradio, e.g. moc:9443). TX-only — never reads fromradio.
        The egress's own channel_index is used (the peer gateway's channel,
        e.g. meshforge=2), not the local-radio channel arg.
        """
        if self._mesh_handler:
            return self._mesh_handler.send_text(message, destination, channel)

        eg = getattr(self.config, 'meshtastic_egress', None)
        if eg and getattr(eg, 'enabled', False) and eg.host:
            try:
                from .meshtastic_protobuf_client import send_text_direct
                return send_text_direct(
                    message,
                    host=eg.host,
                    port=int(eg.port),
                    tls=bool(eg.tls),
                    channel_index=int(eg.channel_index),
                    want_ack=bool(getattr(eg, 'want_ack', True)),
                )
            except Exception as e:
                logger.warning("Meshtastic remote egress failed: %s", e)
                with self._stats_lock:
                    self.stats['errors'] += 1
                return False

        logger.warning("Meshtastic handler not initialized")
        return False

    def send_to_rns(self, message: str, destination_hash: bytes = None) -> bool:
        """Send a message to RNS network via LXMF"""
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
            msg_id = f"lxmf-{int(time.time() * 1000)}"
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
                    msg_id=msg_id, protocol="rns",
                )
                self._maybe_emit_ack_for_msgid(msg_id, kind='delivered')

            def on_failed(receipt):
                reason = "delivery_failed"
                if hasattr(receipt, 'failure_reason'):
                    reason = str(receipt.failure_reason)
                self.delivery_tracker.confirm_failure(msg_id, reason)
                _dc.record(
                    _dc.DeliveryState.DROPPED,
                    msg_id=msg_id, protocol="rns",
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

            call_boundary("rnsd.handle_outbound",
                          self._lxmf_router.handle_outbound, lxm,
                          target=hash_short)
            self.record_send_success(hash_short)
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
                raise RuntimeError(
                    f"circuit open for {_gate_key}: "
                    f"destination temporarily unavailable"
                )

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
                for _ in range(30):
                    if RNS.Transport.has_path(destination_hash):
                        break
                    if self._stop_event.wait(0.1):
                        return False

            if not RNS.Transport.has_path(destination_hash):
                self.record_send_failure(hash_short, "no path")
                return False

            dest_identity = call_boundary("rnsd.identity_recall",
                                          RNS.Identity.recall, destination_hash,
                                          target=hash_short)
            destination = RNS.Destination(
                dest_identity, RNS.Destination.OUT,
                RNS.Destination.SINGLE, "lxmf", "delivery"
            )

            lxm = LXMF.LXMessage(destination, self._lxmf_source, message, "MeshAnchor Gateway")

            # MF Issue #74 port (Fork C syn/ack): pin the LXMF delivery
            # callbacks to the queue row's id (injected at dispatch by
            # process_once) so history_for(msg_id) joins QUEUED (enqueue)
            # → SENT (mark_delivered) → CONFIRMED (delivery proof).
            # Without this, queue-path sends record SENT but never
            # CONFIRMED — biasing the confirmation ring the
            # delivery_confirmation_stall check judges.
            msg_id = (
                payload.get('_queue_msg_id')
                or f"lxmf-{int(time.time() * 1000)}"
            )

            def on_delivered(receipt, _mid=msg_id):
                self.delivery_tracker.confirm_delivery(_mid)
                _dc.record(
                    _dc.DeliveryState.CONFIRMED,
                    msg_id=_mid, protocol="rns",
                )
                self._maybe_emit_ack_for_msgid(_mid, kind='delivered')

            def on_failed(receipt, _mid=msg_id):
                reason = "delivery_failed"
                if hasattr(receipt, 'failure_reason'):
                    reason = str(receipt.failure_reason)
                self.delivery_tracker.confirm_failure(_mid, reason)
                _dc.record(
                    _dc.DeliveryState.DROPPED,
                    msg_id=_mid, protocol="rns",
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

            call_boundary("rnsd.handle_outbound",
                          self._lxmf_router.handle_outbound, lxm,
                          target=hash_short)
            self.record_send_success(hash_short)
            return True

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
        if not self._persistent_queue:
            # Fall back to direct send. ack_required is silently ignored
            # here — direct sends bypass the queue's pending-ack table,
            # so there's nothing to register.
            if dest_type == "meshtastic":
                return "direct" if self.send_to_meshtastic(message, destination, kwargs.get('channel', 0)) else None
            else:
                dest_hash = kwargs.get('destination_hash')
                if isinstance(dest_hash, str):
                    dest_hash = bytes.fromhex(dest_hash)
                return "direct" if self.send_to_rns(message, dest_hash) else None

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
            priority=msg_priority
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

    def _requeue_failed_message(self, msg, destination: str,
                                *, channel_override: Optional[int] = None) -> bool:
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
            if channel_override is not None:
                # Caller (cross-protocol bridge_to_meshcore path) already
                # resolved the destination slot via the config-only
                # resolver. Write it verbatim — do NOT fall through to
                # metadata.channel, which would re-introduce the leak.
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

    def _requeue_failed_chunks(self, chunks, target: str = "meshtastic") -> bool:
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
