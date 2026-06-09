"""Issue #66 application-layer ACK synthesis for RNSMeshtasticBridge.

Part of the 2026-06-09 rns_bridge.py split (MeshForge parity — the same
mixin seams land in the sister repo). RNSMeshtasticBridge is the only
consumer; methods keep their pre-extraction signatures so attribute
access via the bridge instance/class is unchanged.

Host class must provide:
- self._persistent_queue (PersistentMessageQueue or None)
- self._mesh_handler / self._meshcore_handler (or None)
- self.send_to_rns(text, destination_hash=...) (BridgeSendMixin)
- self.stats, self._stats_lock
"""

import logging

logger = logging.getLogger(__name__)


class BridgeAckMixin:
    """Mixin: ACK/correlation/sweep — synthetic delivery receipts (#66)."""

    # Textual ACK forms — see MeshForge sister repo Issue #66 step 3b
    # for the design rationale (human-readable, opt-in, no cross-gateway
    # parser dependency).
    _ACK_TEXT_DELIVERED = "[delivered: {short_id}]"
    _ACK_TEXT_FAILED = "[failed: {short_id}]"
    _ACK_TEXT_TIMEOUT = "[timeout: {short_id}]"

    def _format_ack_text(self, msg_id: str, kind: str) -> str:
        """Compose the operator-visible ACK string. Public for tests."""
        short_id = (msg_id or "")[:8]
        if kind == 'delivered':
            return self._ACK_TEXT_DELIVERED.format(short_id=short_id)
        if kind == 'failed':
            return self._ACK_TEXT_FAILED.format(short_id=short_id)
        if kind == 'timeout':
            return self._ACK_TEXT_TIMEOUT.format(short_id=short_id)
        return f"[{kind}: {short_id}]"

    def _emit_ack_to_origin(
        self,
        msg_id: str,
        origin_network: str,
        origin_address: str,
        kind: str,
    ) -> bool:
        """
        Send a synthetic ACK message back to the origin sender.

        See MeshForge sister-repo docstring for the per-protocol
        dispatch contract — this is the symmetric MeshAnchor side.

        RECURSION INVARIANT (Issue #66 first-caller): synth ACKs MUST NOT
        themselves acquire their own pending-ack record, or a default-on
        deployment of ack_required=True would self-amplify into a feedback
        loop. Today's structural safety: this function dispatches via
        `_mesh_handler.send_text` / `_meshcore_handler.send_text` / direct
        `send_to_rns(... destination_hash=...)`, all of which bypass
        `enqueue_message` and `LXMFBroadcastBridge.on_meshcore_message`
        (the two ack-registration sites). If a future refactor routes
        synth ACKs through either of those paths, the outbound message
        MUST set `metadata['meshforge_is_synth_ack'] = True` AND the
        receiver-side guards in both ack-registration sites MUST consult
        that marker (already wired in LXMFBroadcastBridge.on_meshcore_message
        as of 2026-05-18). Verified by tests in
        tests/test_lxmf_broadcast_ack_first_wins_issue66.py.
        """
        text = self._format_ack_text(msg_id, kind)

        # Issue #66 layer-2 (2026-05-26): suppress channel-origin receipts.
        # A synth ACK whose origin is the bare placeholder "channel:<idx>"
        # has no single addressee — MeshCore/Meshtastic channel broadcasts
        # don't carry per-sender identity, so the only prior way to
        # "deliver" the receipt was to re-broadcast it to the whole
        # channel, putting machine [delivered:]/[failed:]/[timeout:] text
        # on a human-facing channel (operator-observed on the meshanchor
        # public channel). The pending-ack record is still registered and
        # marked at the ack-registration sites, so ack accounting/metrics
        # are unaffected — we only drop the VISIBLE channel-wide receipt.
        # A real DM origin (source_address present) still gets its receipt
        # below. Handled here once for both networks to avoid the
        # asymmetric-handling bug class across the symmetric branches.
        if origin_address.startswith("channel:"):
            with self._stats_lock:
                self.stats.setdefault('synth_ack_channel_origin_suppressed', 0)
                self.stats['synth_ack_channel_origin_suppressed'] += 1
            logger.debug(
                "ack synthesis suppressed: channel-origin receipt (%s) not "
                "broadcast — no single addressee (msg_id=%s kind=%s)",
                origin_address, msg_id[:8], kind,
            )
            return True

        try:
            if origin_network == 'meshtastic':
                if not self._mesh_handler:
                    logger.debug(
                        "ack synthesis skipped: meshtastic handler absent "
                        f"(msg_id={msg_id[:8]} kind={kind})"
                    )
                    return False
                # channel:<idx> origins are suppressed above (no single
                # addressee); only DM-origin receipts reach here.
                return bool(self._mesh_handler.send_text(
                    text, destination=origin_address, channel=0,
                ))
            if origin_network == 'meshcore':
                if not self._meshcore_handler:
                    logger.debug(
                        "ack synthesis skipped: meshcore handler absent "
                        f"(msg_id={msg_id[:8]} kind={kind})"
                    )
                    return False
                # channel:<idx> origins are suppressed above (no single
                # addressee); only DM-origin receipts reach here.
                return bool(self._meshcore_handler.send_text(
                    text, destination=origin_address,
                ))
            if origin_network == 'rns':
                try:
                    dest_hash = bytes.fromhex(origin_address)
                except (TypeError, ValueError):
                    logger.warning(
                        "ack synthesis: bad RNS origin_address hex "
                        f"({origin_address!r}) for msg_id={msg_id[:8]}"
                    )
                    return False
                return bool(self.send_to_rns(text, destination_hash=dest_hash))
            logger.debug(
                f"ack synthesis: unknown origin_network={origin_network!r} "
                f"for msg_id={msg_id[:8]}"
            )
            return False
        except Exception as e:
            logger.warning(
                f"ack synthesis failed for msg_id={msg_id[:8]} "
                f"kind={kind} origin={origin_network}: {e}"
            )
            return False

    def _maybe_emit_ack_for_msgid(self, msg_id: str, kind: str) -> bool:
        """
        Convert delivery proof (or failure) into a synthetic ACK back to
        the origin sender — if and only if the message was registered
        via PersistentMessageQueue.register_pending_ack().

        Idempotent: mark_acked() returns None on the second call.
        """
        if not self._persistent_queue:
            return False
        try:
            origin = self._persistent_queue.mark_acked(msg_id)
        except Exception as e:
            logger.warning(
                f"ack synthesis: mark_acked failed for {msg_id[:8]}: {e}"
            )
            return False
        if not origin:
            return False
        return self._emit_ack_to_origin(
            msg_id,
            origin_network=origin['origin_network'],
            origin_address=origin['origin_address'],
            kind=kind,
        )

    def _sweep_overdue_acks(self) -> int:
        """
        Find pending-ack records past their deadline; emit a synthetic
        TIMEOUT ACK for each + finalise via mark_timeout().

        Called periodically from _bridge_loop (every ~30s). Returns the
        count of TIMEOUT ACKs emitted this sweep.
        """
        if not self._persistent_queue:
            return 0
        try:
            overdue = self._persistent_queue.find_overdue_acks()
        except Exception as e:
            logger.warning(f"ack sweep: find_overdue_acks failed: {e}")
            return 0
        emitted = 0
        for row in overdue:
            msg_id = row['message_id']
            try:
                if not self._persistent_queue.mark_timeout(msg_id):
                    continue
            except Exception as e:
                logger.warning(
                    f"ack sweep: mark_timeout failed for {msg_id[:8]}: {e}"
                )
                continue
            self._emit_ack_to_origin(
                msg_id,
                origin_network=row['origin_network'],
                origin_address=row['origin_address'],
                kind='timeout',
            )
            emitted += 1
        if emitted:
            logger.info(f"ack sweep: emitted {emitted} TIMEOUT ACK(s)")
        return emitted
