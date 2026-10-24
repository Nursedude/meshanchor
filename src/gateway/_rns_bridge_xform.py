"""RNSMeshtasticBridge mixin: the two message transforms.

Split out of ``rns_bridge.py`` (2026-10-23) when that file sat at 1,487
lines, 13 under the MF025 cap, and BIC-2 slice 2b needs room in exactly
these methods. Mirrors MeshForge's ``gateway/_rns_bridge_xform.py``
(``MessageTransformMixin``) so the twins' 2b port lines up file by file.

Behaviour-neutral move: ``_process_mesh_to_rns`` and ``_process_rns_to_mesh``
are byte-identical to their previous bodies except three lines at the top
of ``_process_rns_to_mesh`` that read ``HAS_PERSISTENT_QUEUE`` /
``MessagePriority`` back from ``rns_bridge`` at call time (both come from a
``safe_import`` there, and tests patch ``gateway.rns_bridge.*``).

Host contract: the RNSMeshtasticBridge attributes the bodies use (config,
stats, health, router, handlers, queue, send/requeue helpers).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base_handler import (chunk_for_mesh, get_rf_tx_registry, is_already_bridged,
                           is_rns_to_mesh_echo, strip_unverified_mark)

if TYPE_CHECKING:  # annotations only — rns_bridge imports this module
    from .rns_bridge import BridgedMessage

# The host module's logger name, not __name__: the moved log lines must keep
# their journal prefix ([gateway.rns_bridge]) for anyone grepping it.
logger = logging.getLogger("gateway.rns_bridge")


class MessageTransformMixin:
    def _process_mesh_to_rns(self, msg: BridgedMessage):
        """Process message from Meshtastic to RNS.

        On send failure for non-broadcast messages, attempts to persist
        to the persistent queue for later retry.
        """
        # Echo guard — is_already_bridged() carries the why (#151). Before the
        # accounting: an echo was never an attempt. Witness, never silent.
        if is_already_bridged(msg.content):
            with self._stats_lock:
                self.stats['mesh_to_rns_echo_suppressed'] = (
                    self.stats.get('mesh_to_rns_echo_suppressed', 0) + 1)
            logger.info(
                "Mesh→RNS echo suppressed — already bridge-tagged "
                "(from %s): %r", msg.source_id or "?", str(msg.content)[:48])
            return

        # Every message ends in exactly ONE of delivered / dropped (see
        # _m2r_accounting). A replay of an attempted message claims its
        # origin: the attempt was already counted.
        meta0 = msg.metadata if isinstance(msg.metadata, dict) else {}
        attempt_counted = self._m2r_claim_replay(meta0.get('m2r_origin'))
        if not attempt_counted:
            with self._stats_lock:
                self.stats['mesh_to_rns_attempted'] = (
                    self.stats.get('mesh_to_rns_attempted', 0) + 1)
        outcome_counted = False
        sent_count = 0  # read by the except path: never requeue a sent copy
        try:
            prefix = f"[Mesh:{msg.source_id[-4:]}] " if msg.source_id else "[Mesh] "
            content = prefix + msg.content

            # Build destination list. Direct DM → single recipient; broadcast
            # fans out to every default_lxmf_destination configured. A DM whose
            # peer didn't resolve is REFUSED (bridge matrix F14, operator
            # 2026-10-16, MF lead): the default list includes peer gateways
            # that re-emit on RF. — str OR list, via get_lxmf_destinations(). Ported from
            # MeshForge (lead repo): the str-only path silently ignored a list config,
            # dropping every broadcast despite default_lxmf_destination being set.
            destinations = []
            if msg.destination_id and not msg.is_broadcast:
                direct = self._get_rns_destination(msg.destination_id)
                if direct:
                    destinations.append(direct)

            if not destinations and msg.destination_id and not msg.is_broadcast:
                with self._stats_lock:
                    self.stats['mesh_to_rns_private_refused'] = (
                        self.stats.get('mesh_to_rns_private_refused', 0) + 1)
                    self.stats['mesh_to_rns_dropped'] = (
                        self.stats.get('mesh_to_rns_dropped', 0) + 1)
                outcome_counted = True
                logger.warning(
                    f"Mesh→RNS: refusing DM from {msg.source_id} to "
                    f"{msg.destination_id} — no single RNS recipient; a DM is "
                    f"never fanned to the default list (F14)")
                self.health.record_message_failed("mesh_to_rns", requeued=False)
                return

            if not destinations:
                for hex_str in self.config.rns.get_lxmf_destinations():
                    try:
                        destinations.append(bytes.fromhex(hex_str))
                    except ValueError:
                        logger.warning(f"Invalid default_lxmf_destination hex: {hex_str!r}")

            sent_count = 0
            failed_legs = []
            for dest_hash in destinations:
                if self.send_to_rns(content, dest_hash):
                    sent_count += 1
                else:
                    failed_legs.append(dest_hash)

            if sent_count and failed_legs:
                # F4 review R5: one leg landing must not erase a sibling's
                # retry — requeue only the legs that failed.
                # No origin: the message is delivered (counted below); these
                # rows' outcomes must not count it again.
                if not self._requeue_failed_message(
                        msg, "rns", rns_destinations=failed_legs,
                        rns_message=content):
                    # Dedup/queue-full drop: partly delivered, so no health
                    # failure — but never silent (review F3, MF lead).
                    with self._stats_lock:
                        self.stats['mesh_to_rns_requeue_dropped'] = (
                            self.stats.get('mesh_to_rns_requeue_dropped', 0) + 1)
                    logger.warning(
                        "Mesh→RNS: retry of failed leg(s) "
                        f"{[d.hex()[:8] for d in failed_legs]} NOT queued "
                        "(dedup/queue)")

            if sent_count:
                if len(destinations) > 1:
                    logger.info(f"Bridge Mesh→RNS: {sent_count}/{len(destinations)} dest(s) — {content[:50]}...")
                else:
                    logger.info(f"Bridge Mesh→RNS: {content[:50]}...")
                self._m2r_count('delivered')
                outcome_counted = True
                self.health.record_message_sent("mesh_to_rns")
            elif msg.is_broadcast:
                # No default_lxmf_destination configured, or all peers unreachable —
                # broadcast best-effort, debug-only (LXMF is point-to-point).
                # Still a message that did not arrive: counted dropped.
                logger.debug(f"Mesh→RNS broadcast not delivered: {content[:30]}...")
                self._m2r_count('dropped')
                outcome_counted = True
            else:
                logger.warning(f"Failed to bridge Mesh→RNS: {content[:30]}...")
                with self._stats_lock:
                    self.stats['errors'] += 1
                # F4: retry each failed leg at the hash it already resolved
                # to — never re-resolve, which could widen a DM's audience.
                # Requeued = PENDING (the queue reports the outcome); only an
                # un-queueable failure is dropped now.
                requeued = self._requeue_failed_message(
                    msg, "rns", rns_destinations=destinations,
                    rns_message=content, origin=self._m2r_new_origin())
                if not requeued:
                    self._m2r_count('dropped')
                outcome_counted = True
                self.health.record_message_failed("mesh_to_rns", requeued=requeued)

        except Exception as e:
            logger.error(f"Error bridging Mesh→RNS: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            try:
                self.health.record_error("rns", e)
            except Exception:  # noqa: BLE001 — the outcome below must land
                logger.exception("Mesh→RNS: health.record_error raised")
            requeued = False
            if sent_count:
                # Already on the wire: a retry would be a duplicate.
                logger.warning("Mesh→RNS: raised after a send — not requeued")
                if not outcome_counted:
                    self._m2r_count('delivered')
            elif outcome_counted:
                pass
            elif (msg.metadata or {}).get('m2r_replay'):
                logger.warning("Mesh→RNS: replay raised again — not requeued")
                self._m2r_count('dropped')
            else:
                requeued = self._requeue_failed_message(
                    msg, "rns", origin=self._m2r_new_origin())
                if not requeued:
                    self._m2r_count('dropped')
            try:
                self.health.record_message_failed("mesh_to_rns",
                                                  requeued=requeued)
            except Exception:  # noqa: BLE001 — never escape after claiming
                logger.exception("Mesh→RNS: health.record_message_failed raised")

    def _process_rns_to_mesh(self, msg: BridgedMessage):
        """Process message from RNS to Meshtastic.

        In mqtt_bridge mode, routes through the persistent queue for
        reliable delivery with retry. Otherwise sends directly and
        persists to queue on failure.

        Oversize content is split with ``chunk_for_mesh`` (ported from
        MeshForge `0066470`) so multi-line RNS content (NomadNet, or a bot
        reply bridged in over RNS) isn't silently truncated to one packet by
        the handler's ``_truncate_if_needed``. Short content yields a single
        chunk == content, so the common path is unchanged.
        """
        # Split out of rns_bridge.py (MF025): read these module flags back
        # at call time so tests that patch gateway.rns_bridge.* still
        # reach this code (twin of MeshForge's _rns_bridge_xform).
        from . import rns_bridge as _rns_bridge_module
        HAS_PERSISTENT_QUEUE = _rns_bridge_module.HAS_PERSISTENT_QUEUE
        MessagePriority = _rns_bridge_module.MessagePriority
        try:
            # Echo guard — is_rns_to_mesh_echo() carries the why. Witness,
            # never a silent drop (honest_failure_modes #9).
            if is_rns_to_mesh_echo(msg.content):
                with self._stats_lock:
                    self.stats['rns_to_mesh_echo_suppressed'] = (
                        self.stats.get('rns_to_mesh_echo_suppressed', 0) + 1)
                logger.info(
                    "RNS→Mesh echo suppressed — already bridge-tagged "
                    "(from %s): %r",
                    msg.source_id[:8] if msg.source_id else "?",
                    str(msg.content)[:48])
                return

            # an unverified LXMF source is a CLAIM: tag it [RNS:?xxxx]
            _q = "?" if (msg.metadata or {}).get("source_verified") is False else ""
            prefix = f"[RNS:{_q}{msg.source_id[:4]}] "
            _body = msg.content
            if _q:
                _body = strip_unverified_mark(_body)  # the tag carries the mark
            content = prefix + _body

            # Dual-path dedup (gated, default off; mirror of MeshForge
            # 1494e8f). On a box whose LOCAL mesh_bridge also carries this
            # RF traffic, a broadcast the mesh_bridge already transmitted
            # arrives here a second time via a peer's RNS relay and would
            # land on the radio TWICE. Suppress ONLY on a registry hit —
            # when the local radio missed the message on RF there is no hit
            # and this copy still delivers (the relay's fallback value).
            if (self._dual_path_dedup_on()
                    and get_rf_tx_registry().seen_within(
                        _body, self._dual_path_dedup_window())):
                with self._stats_lock:
                    self.stats['rns_to_mesh_dual_path_suppressed'] = (
                        self.stats.get('rns_to_mesh_dual_path_suppressed', 0) + 1)
                logger.info(
                    f"Bridge RNS→Mesh suppressed (dual-path dedup — already "
                    f"on RF via mesh_bridge): {msg.content[:50]}...")
                return

            # The [RNS:xxxx] prefix rides EVERY chunk (prefix= reserves its
            # bytes; mirror of MeshForge f02ad82) — the tag is the echo-loop
            # invariant, and the chunk-0-only shape leaked untagged tail
            # chunks through the loop guards on every gateway.
            # _body, never msg.content: the "?" in the tag IS the mark, so
            # the wire must not carry UNVERIFIED_MARK a second time (review
            # of the echo fix, 2026-10-16 — the strip fed only the log line).
            chunks = chunk_for_mesh(_body, prefix=prefix)
            multi = f" [{len(chunks)} chunks]" if len(chunks) > 1 else ""

            # In mqtt_bridge mode, enqueue each chunk as its own queue item
            # (independent retry) under "meshtastic": queue_send -> HTTP
            # toradio, never a broker publish (#151 item 5). NOTE: meshanchor-server runs
            # bridge_mode="meshcore_bridge", so this branch is inactive there;
            # the direct path below is the live one.
            if (self._persistent_queue
                    and self.config.bridge_mode == "mqtt_bridge"
                    and HAS_PERSISTENT_QUEUE):
                enqueued = 0
                for chunk in chunks:
                    payload = {
                        'message': chunk,
                        'channel': self.config.meshtastic.channel,
                        'source_id': msg.source_id,
                    }
                    if self._persistent_queue.enqueue(
                        payload=payload,
                        destination="meshtastic",
                        priority=MessagePriority.NORMAL,
                        source="rns",
                    ):
                        enqueued += 1
                if enqueued:
                    logger.info(f"Bridge RNS→Mesh (queued{multi}): {content[:50]}...")
                    with self._stats_lock:
                        self.stats['messages_rns_to_mesh'] += 1
                    self.health.record_message_sent("rns_to_mesh")
                    return
                # Nothing queued (all deduped/rejected) — fall through to a
                # direct send attempt, mirroring the original None→fallthrough.

            # Direct send (non-MQTT mode or queue unavailable). Send every
            # chunk; success requires all of them to go out.
            failed_chunks = [
                chunk for chunk in chunks
                if not self.send_to_meshtastic(
                    chunk, channel=self.config.meshtastic.channel)
            ]
            if chunks and not failed_chunks:
                logger.info(f"Bridge RNS→Mesh{multi}: {content[:50]}...")
                with self._stats_lock:
                    self.stats['messages_rns_to_mesh'] += 1
                self.health.record_message_sent("rns_to_mesh")
            else:
                sent = len(chunks) - len(failed_chunks)
                logger.warning(
                    f"Failed to bridge RNS→Mesh ({sent}/{len(chunks)} chunks sent)"
                )
                with self._stats_lock:
                    self.stats['errors'] += 1
                # Re-queue ONLY the failed chunks — each already byte-bounded —
                # so the retry preserves the full content instead of re-sending
                # the whole un-chunked original (which the retry would truncate)
                # and without re-transmitting chunks that already went out.
                requeued = self._requeue_failed_chunks(
                    failed_chunks or chunks, "meshtastic",
                    source=self._leg_source(msg))
                self.health.record_message_failed("rns_to_mesh", requeued=requeued)

        except Exception as e:
            logger.error(f"Error bridging RNS→Mesh: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            self.health.record_error("meshtastic", e)
            self._requeue_failed_message(msg, "meshtastic")
            self.health.record_message_failed("rns_to_mesh", requeued=True)
