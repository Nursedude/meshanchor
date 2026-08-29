"""Directed-DM ack feedback — handler-side mixin (prototype 2026-08-28).

The MeshCoreHandler half of the syn/ack loop described in
``meshcore_dm_reply.py``: register a sent DM's ``expected_ack`` so the ACK
event can be correlated, and feed the outcome back to the originating mesh
as a ``[MC:ack]`` bridge notice. Split out of ``meshcore_handler.py`` per
the 1,500-line cap (MF025) — same pattern as ``MeshCoreRadioOpsMixin``.

Expects on the host class:
- self._dm_acks: PendingDmAcks
- self._message_queue / self._message_callback (MeshCore→bridge fan-out)
- self.stats / self._stats_lock
"""

import logging
from queue import Full
from typing import Any, Dict

from .canonical_message import CanonicalMessage, Protocol
from .meshcore_dm_reply import ack_code_hex

logger = logging.getLogger(__name__)


class MeshCoreDmAckMixin:
    """Ack-watch registration + [MC:ack] notice emission for directed DMs."""

    def _register_dm_ack_watch(self, send_evt: Any, destination: str,
                               reply_ctx: Dict[str, Any]) -> None:
        """Register a sent DM's expected_ack for ACK correlation.

        A send result without a usable expected_ack (simulator, API drift)
        is witnessed by a stat and NOT watched — no ack notice will ever be
        emitted for it, which is honest: we cannot claim a delivery we
        cannot correlate. Never raises (the DM itself already succeeded).
        """
        try:
            payload = getattr(send_evt, 'payload', None)
            exp = payload.get('expected_ack') if isinstance(payload, dict) else None
            code = ack_code_hex(exp)
            if not code:
                with self._stats_lock:
                    self.stats.setdefault('meshcore_dm_ack_unwatchable', 0)
                    self.stats['meshcore_dm_ack_unwatchable'] += 1
                logger.debug(
                    f"DM to {destination!r} sent but expected_ack absent — "
                    f"delivery will be unconfirmed")
                return
            timeout_ms = 5000
            if isinstance(payload, dict):
                try:
                    timeout_ms = int(payload.get('suggested_timeout') or 5000)
                except (TypeError, ValueError):
                    pass
            # 3x the radio's suggestion: path retries and store-and-forward
            # legitimately arrive late, and a late ACK matching a live watch
            # is still a true delivery.
            self._dm_acks.register(code, reply_ctx,
                                   timeout_s=(timeout_ms / 1000.0) * 3)
            with self._stats_lock:
                self.stats.setdefault('meshcore_dm_ack_watch', 0)
                self.stats['meshcore_dm_ack_watch'] += 1
        except Exception as e:
            logger.debug(f"DM ack-watch registration failed: {e}")

    def _correlate_dm_ack(self, payload: Any) -> None:
        """Match an ACK event against watched DMs; emit the ✓ notice on a hit.

        Unmatched ACKs (other traffic, or a watch that aged out) fall
        through silently — absence of a watch is not an error.
        """
        code = ""
        if isinstance(payload, dict):
            code = ack_code_hex(payload.get('code'))
        ctx = self._dm_acks.pop(code) if code else None
        if ctx is not None:
            with self._stats_lock:
                self.stats.setdefault('meshcore_dm_ack_confirmed', 0)
                self.stats['meshcore_dm_ack_confirmed'] += 1
            self._emit_dm_notice(
                f"✓ {ctx.get('contact', '?')} received the reply "
                f"from {ctx.get('origin', '?')}", ctx)

    def _emit_dm_notice(self, text: str, reply_ctx: Dict[str, Any]) -> None:
        """Feed a directed-DM outcome back to the originating mesh.

        Injected into the MeshCore→bridge queue with source_address 'ack',
        so _process_meshcore_to_bridge prefixes it '[MC:ack] ' and fans it
        out to Meshtastic + RNS exactly like any MeshCore-origin traffic.
        The '[MC:' marker doubles as the split-horizon guard: the notice can
        never be re-injected onto MeshCore. A full queue leaves a stat
        witness — the notice is best-effort, the DM itself already stands.
        """
        try:
            notice = CanonicalMessage(
                content=text,
                source_address="ack",
                source_network=Protocol.MESHCORE.value,
                is_broadcast=False,
            )
            notice.metadata['dm_reply_notice'] = dict(reply_ctx)
            if self._message_queue is not None:
                self._message_queue.put_nowait(notice)
            if self._message_callback:
                try:
                    self._message_callback(notice)
                except Exception as e:
                    logger.debug(f"DM notice callback error: {e}")
        except Full:
            with self._stats_lock:
                self.stats.setdefault('meshcore_dm_notice_dropped_full', 0)
                self.stats['meshcore_dm_notice_dropped_full'] += 1
            logger.warning("MeshCore→bridge queue full — DM notice dropped")
        except Exception as e:
            logger.debug(f"DM notice emit failed: {e}")
