"""MeshCore egress — handler-side mixin (split 2026-10-24, MF025).

Every way a message LEAVES on the MeshCore radio: the bridge's synchronous
``send_text`` / persistent-queue ``queue_send`` entry points, the async
``_process_outbound`` worker that drains the send queue, ``_send_message``
(the RF egress chokepoint: tx_guard, contact resolution, the DM-never-
broadcast rule) and the delivery-record helpers ``_record_tx`` /
``_companion_refused``. One seam with one meaning — egress and its
accounting — so BIC-2's per-leg source threads through a single module
instead of being scattered across the handler. Moved byte-for-byte out of
``meshcore_handler.py`` (which re-exports the two helpers); same pattern as
``MeshCoreDmAckMixin`` / ``MeshCoreRadioOpsMixin``.

Expects on the host class:
- self._meshcore / self._connected / self._send_queue
- self.stats / self._stats_lock / self.health
- self._notice_dm_refused / self._emit_dm_notice / self._register_dm_ack_watch
  (MeshCoreDmAckMixin) and self.record_chat_message
"""

import logging
from queue import Empty, Full
from typing import Any, Dict, List, Optional

from utils.boundary_timing import timed_boundary
from utils.tx_guard import TransmitBlocked

from .canonical_message import CanonicalMessage, Protocol
from .meshcore_dm_reply import companion_error
from .meshcore_simulator import MeshCoreSimulator
from .message_queue_models import SendRefused

# The handler's name, not __name__: egress logs kept their identity when
# this split out of meshcore_handler.py (2026-10-24) — the journal, caplog
# filters and operators' greps all key on it. Same as _meshcore_ingress.
logger = logging.getLogger("gateway.meshcore_handler")


def _record_tx(state: str, msg_id: Optional[str], reason: Optional[str] = None,
               note: str = "", source: Optional[str] = None) -> None:
    """MeshCore egress → delivery_counters (2026-09-23): the primary radio's
    sends never reached the Delivery screen, so a busy leg read as silence.
    Lazy import — the module-level form deadlocked threaded startup
    (bridge_send_mixin._LazyDeliveryCounters). record() never raises.
    ``source`` names the BIC-2 leg ``source>meshcore``; a send with no id
    gets a collision-free synthesized one (MeshForge 7d93de4d #3)."""
    try:
        from gateway import delivery_counters as dc
        from gateway.delivery_legs import synth_id
        dc.record(dc.DeliveryState(state), msg_id or synth_id("meshcore"),
                  protocol="meshcore", source=source,
                  drop_reason=dc.DropReason(reason) if reason else None,
                  note=note[:80])
    except Exception as e:
        logger.warning(f"MeshCore delivery record failed ({state}): {e}")


# The wire's slot field is ONE byte: meshcore_py 2.3.7 send_chan_msg encodes
# ``chan.to_bytes(1, "little")`` (measured on meshanchor-server 2026-10-24).
# A radio may carry fewer slots (DEVICE_INFO max_channels) — that refusal is
# the companion's ERROR Event, recorded by _companion_refused.
MESHCORE_MAX_SLOT = 255


def queue_row_slot(row: Dict) -> int:
    """The explicit MeshCore slot a queued CHANNEL row may air on, or -1.

    ONE predicate for every refusal gate (the queue's ``validate_fn`` at
    enqueue, ``queue_send`` for rows already queued, the async worker as
    backstop — hfm #5). Only the top-level ``channel`` counts:
    ``metadata['channel']`` is the SOURCE protocol's index (Issue #37 class).
    Only an int in 0..MESHCORE_MAX_SLOT is a slot: not a bool (True is an
    int), not a float (int(1.5) == 1), not a string. Anything else → -1.
    """
    raw = row.get('channel')
    if isinstance(raw, bool) or not isinstance(raw, int):
        return -1
    return raw if 0 <= raw <= MESHCORE_MAX_SLOT else -1


def validate_queue_row(row: Dict) -> Optional[str]:
    """The queue's ``validate_fn`` for ``meshcore``: refusal reason or None.

    A DM row (``destination`` set) airs to its contact and needs no slot; a
    CHANNEL row needs an explicit one (``queue_row_slot``). Asked by
    ``PersistentMessageQueue.enqueue`` so a slot-less row is never written;
    ``queue_send`` asks the same predicate as backstop for rows already
    queued (Issue #37 class, 2026-10-24).
    """
    if row.get('destination') or queue_row_slot(row) >= 0:
        return None
    return (f"MeshCore channel row with no valid slot (an int "
            f"0..{MESHCORE_MAX_SLOT}; got channel={row.get('channel')!r}); "
            f"never aired on a source slot or slot 0 (Public)")


def _companion_refused(err: str, msg_id: Optional[str], what: str,
                       source: Optional[str] = None) -> bool:
    """meshcore_py never raises on a failed send: the ERROR Event IS the
    failure (review 2026-09-23 — it was being recorded as SENT)."""
    logger.warning(f"MeshCore {what} refused by the companion: {err}")
    _record_tx("dropped", msg_id, "non_retriable_error", f"companion {err}",
               source=source)
    return False

class MeshCoreOutboundMixin:
    """Send entry points, the async outbound worker and the RF egress chokepoint."""

    async def _process_outbound(self) -> None:
        """Process outbound messages from the bridge → MeshCore."""
        try:
            msg = self._send_queue.get_nowait()
        except Empty:
            return

        try:
            channel = 0
            reply_ctx = None
            msg_id = None
            queued = False
            source = None   # BIC-2 leg origin; None counts under ?>meshcore
            if isinstance(msg, CanonicalMessage):
                msg_id = msg.id
                source = msg.metadata.get('leg_source')
                text = msg.to_meshcore_text()
                dest = msg.destination_address
                reply_ctx = msg.metadata.get('dm_reply_ctx')
                # MeshCore channel slot rides on metadata since
                # CanonicalMessage is protocol-agnostic. send_text() stashes
                # it; routing fallbacks default to slot 0 (Public).
                raw_channel = msg.metadata.get('channel')
                channel = self._resolve_channel(
                    raw_channel, source=f"CanonicalMessage dest={dest!r}"
                )
            elif isinstance(msg, dict):
                text = msg.get('message', '')
                dest = msg.get('destination')
                # A persistent-queue row: the queue's mark_delivered records
                # SENT on _queue_msg_id when queue_send hands it here, so the
                # worker records only a FAILED outcome, on the row's own id
                # (MeshForge c2024ca7 R1 / 7d93de4d #2: reading msg['id']
                # minted a fresh id and counted the row sent twice).
                queued = bool(msg.get('_queue_msg_id'))
                msg_id = msg.get('_queue_msg_id') or msg.get('id')
                from gateway.delivery_legs import payload_source
                source = payload_source(msg)
                meta = msg.get('metadata')
                if isinstance(meta, dict):
                    reply_ctx = meta.get('dm_reply_ctx')
                # Chokepoint 2 of 2 (2026-10-24, Issue #37 class): a queued
                # CHANNEL row airs only on its explicit top-level slot. This
                # REVERSES the 2026-05-20 metadata.channel fallback: that
                # value is the SOURCE protocol's index, and _resolve_channel's
                # default is 0 = Public — both put private cargo on Public.
                # Refused, with a witness on the row's own id and leg.
                raw_channel = msg.get('channel')
                if not dest:
                    channel = queue_row_slot(msg)
                    if channel < 0:
                        # Reachable only by a path that skipped queue_send.
                        # Same drop_reason as the queue's two gates (#5).
                        self._note_no_slot_refusal(msg_id, raw_channel)
                        _record_tx("dropped", msg_id, "unsendable",
                                   "queue row: no explicit slot", source=source)
                        return
                else:
                    channel = self._resolve_channel(
                        raw_channel, source=f"dict dest={dest!r}")
            else:
                text = str(msg)
                dest = None

            success = await self._send_message(text, dest, channel=channel,
                                               reply_ctx=reply_ctx, msg_id=msg_id,
                                               source=source, queued=queued)

            if success:
                with self._stats_lock:
                    self.stats.setdefault('meshcore_tx', 0)
                    self.stats['meshcore_tx'] += 1
                self.health.record_message_sent("to_meshcore")
            else:
                with self._stats_lock:
                    self.stats.setdefault('errors', 0)
                    self.stats['errors'] += 1

        except TransmitBlocked as e:
            # Deliberate catch (see tx_guard docstring): the refusal is
            # already recorded+logged by the guard; letting it fly would kill
            # the outbound task mid-bookkeeping (the finding-5 class). The
            # message is dropped, witnessed by the stat.
            with self._stats_lock:
                self.stats.setdefault('tx_blocked', 0)
                self.stats['tx_blocked'] += 1
            logger.warning(
                f"MeshCore outbound refused by tx_guard — dropped: {e}")
        except Exception as e:
            logger.error(f"Error processing outbound MeshCore message: {e}")
            # It must not vanish from the Delivery record (review 2026-09-23).
            _record_tx("dropped", msg_id, "non_retriable_error", f"outbound: {e}",
                       source=source)

    async def _send_message(
        self,
        text: str,
        destination: Optional[str] = None,
        channel: int = 0,
        reply_ctx: Optional[Dict[str, Any]] = None,
        msg_id: Optional[str] = None,
        source: Optional[str] = None,
        queued: bool = False,
    ) -> bool:
        """
        Send a text message to the MeshCore network.

        Args:
            text: Message text (will be truncated to 160 bytes if needed)
            destination: Destination address (None = channel broadcast)
            channel: Channel slot for broadcasts (0 = Public; 1+ = private
                slots set up via meshcore_set_channel.py / Node-Connect).
            reply_ctx: directed-DM reply context — when set on a DM, the
                send result's expected_ack is registered so _on_ack can
                feed the delivery back as a [MC:reply] notice, and a
                contact-not-found drop emits a negative notice instead of
                leaving the addressed reply a silent void.

            source: BIC-2 leg origin — every outcome lands on ``source>meshcore``.
            queued: a persistent-queue row, whose SENT the queue already
                recorded; only a failure is recorded here.

        Returns:
            True if sent successfully.
        """
        def _rec(*a, **k):   # BIC-2: every outcome on this send's leg
            if queued and a and a[0] == "sent":
                return       # the persistent queue already recorded SENT
            _record_tx(*a, source=source, **k)

        if not self._meshcore or not self._connected:
            _rec("dropped", msg_id, "non_retriable_error", "not connected")
            return False

        # RF egress chokepoint — every MeshCore send funnels through here,
        # and the companion is a real LoRa radio (2026-08-09 MF review: this
        # radio sat entirely outside the egress architecture — and it is
        # MeshAnchor's PRIMARY). The in-process simulator is not egress.
        # OUTSIDE the try, so the refusal cannot be absorbed into
        # "send failed".
        if not isinstance(self._meshcore, MeshCoreSimulator):
            from utils.tx_guard import assert_meshcore_tx_allowed
            assert_meshcore_tx_allowed(
                kind="meshcore_tx",
                detail=f"meshcore_handler send dest={destination!r} "
                       f"text={text[:40]!r}")

        try:
            if destination:
                # Direct message — resolve the contact first. get_contacts()
                # returns an Event whose payload is the actual dict/list of
                # contacts; older code iterated the Event itself, which
                # raised "Event object is not iterable".
                if hasattr(self._meshcore, 'commands'):
                    with timed_boundary("meshcore.get_contacts"):
                        contacts_evt = await self._meshcore.commands.get_contacts()
                    contacts = self._extract_contacts(contacts_evt)
                    contact = self._find_contact(contacts, destination)
                    if contact:
                        with timed_boundary("meshcore.send_msg",
                                            target=destination):
                            send_evt = await self._meshcore.commands.send_msg(contact, text)
                        err = companion_error(send_evt)
                        if err is not None:
                            self._notice_dm_refused(destination, err, reply_ctx)
                            return _companion_refused(err, msg_id, f"DM {destination!r}",
                                              source=source)
                        if reply_ctx is not None:
                            self._register_dm_ack_watch(
                                send_evt, destination, reply_ctx)
                        _rec("sent", msg_id, note=f"dm {destination}")
                        return True
                # Contact resolution failed (advert aged out, peer never
                # seen, etc.) — drop the DM rather than broadcasting it.
                # See channel-0 Public leak (2026-05-19): a DM addressed
                # to a specific recipient must never be aired to a
                # channel audience on contact-not-found, because the
                # default-channel cascade lands it on slot 0 (Public).
                # Privacy bug independent of which channel; broadcast
                # was never an intended fallback for a misdirected DM.
                with self._stats_lock:
                    self.stats.setdefault('meshcore_dm_dropped_contact_not_found', 0)
                    self.stats['meshcore_dm_dropped_contact_not_found'] += 1
                logger.warning(
                    f"MeshCore DM dropped — contact not found for "
                    f"{destination!r}. Pre-fix behavior was to broadcast "
                    f"on slot {channel}, which leaked to Public; "
                    f"see persistent_issues channel-0-leak-2026-05-19."
                )
                if reply_ctx is not None:
                    # An addressed reply must not vanish silently: tell the
                    # originating mesh the address didn't resolve.
                    self._emit_dm_notice(
                        f"✗ no MeshCore contact matching "
                        f"'{destination}' — reply not delivered", reply_ctx)
                _rec("dropped", msg_id, "destination_unreachable",
                           f"no contact {destination}")
                return False
            else:
                # Channel broadcast
                if hasattr(self._meshcore, 'commands'):
                    with timed_boundary("meshcore.send_chan_msg",
                                        target=str(channel)):
                        chan_evt = await self._meshcore.commands.send_chan_msg(
                            channel, text)
                    err = companion_error(chan_evt)
                    if err is not None:
                        return _companion_refused(err, msg_id, f"ch{channel}",
                                              source=source)
                elif hasattr(self._meshcore, 'send_channel_txt_msg'):
                    # Simulator: not egress, so never a delivery record (review A F5).
                    with timed_boundary("meshcore.send_chan_msg",
                                        target=str(channel)):
                        await self._meshcore.send_channel_txt_msg(text)
                    return True
                else:
                    logger.error("MeshCore instance has no send method")
                    _rec("dropped", msg_id, "non_retriable_error", "no send method")
                    return False
                _rec("sent", msg_id, note=f"ch{channel}")
                return True

        except Exception as e:
            logger.error(f"Failed to send MeshCore message: {e}")
            _rec("dropped", msg_id, "non_retriable_error", str(e))
            return False

    @staticmethod
    def _resolve_channel(raw_value: Any, source: str) -> int:
        """Normalize a channel metadata value, surfacing missing/unparsable
        cases at DEBUG.

        The pre-2026-05-19 pattern ``int(meta.get('channel', 0) or 0)``
        silently coerced None / '' / missing-key / unparsable values to 0
        (Public), which masked the call site that should have set the
        channel explicitly. After the channel-0 Public leak fix, the DM
        side no longer broadcasts on contact-not-found, but explicit
        broadcasts still default to 0 when the caller forgets to set it.
        Logging surfaces those callers at DEBUG so they can be fixed
        without changing routing behaviour.
        """
        if raw_value is None or raw_value == '':
            logger.debug(
                f"_process_outbound: {source} missing channel metadata, "
                f"defaulting to slot 0 (Public)"
            )
            return 0
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            logger.debug(
                f"_process_outbound: {source} unparsable channel "
                f"{raw_value!r}, defaulting to slot 0 (Public)"
            )
            return 0

    @staticmethod
    def _extract_contacts(contacts_evt: Any) -> List[Any]:
        """Pull the contact list out of meshcore_py's get_contacts Event.

        Real Event has `.payload` (dict keyed by name or list); simulator
        returns a plain list. Both shapes flow through here.
        """
        if contacts_evt is None:
            return []
        payload = getattr(contacts_evt, 'payload', contacts_evt)
        if isinstance(payload, dict):
            return list(payload.values())
        if isinstance(payload, list):
            return payload
        # Unknown shape — be defensive, don't iterate the Event itself.
        return []

    def _find_contact(self, contacts: List[Any], address: str) -> Optional[Any]:
        """
        Find a MeshCore contact matching the given address.

        Args:
            contacts: List of contact objects from meshcore_py
            address: Address to match (pubkey prefix or name)

        Returns:
            Matching contact object or None.
        """
        if not contacts:
            return None

        for contact in contacts:
            # Match by public key prefix
            if isinstance(contact, dict):
                pk = contact.get('public_key', b'')
                name = contact.get('adv_name', '')
            else:
                pk = getattr(contact, 'public_key', b'')
                name = getattr(contact, 'adv_name', '')

            if isinstance(pk, bytes):
                pk_hex = pk.hex()
            else:
                pk_hex = str(pk)

            if address in pk_hex or address == name:
                return contact

        return None

    def send_text(self, message: str, destination: str = None,
                  channel: int = 0,
                  reply_ctx: Optional[Dict[str, Any]] = None,
                  *, source: Optional[str] = None) -> bool:
        """
        Send a text message to MeshCore (synchronous interface).

        Queues the message for async processing in the event loop.

        Args:
            message: Text content to send
            destination: Destination address (None for broadcast)
            channel: Channel index (MeshCore uses channels differently)
            reply_ctx: directed-DM reply context ({contact, origin}) — when
                set on a DM, the send's expected_ack is watched and the
                delivery outcome fed back as a [MC:reply] bridge notice.
            source: the transport the message came from — rides metadata
                ``leg_source`` to the worker so every outcome counts on the
                BIC-2 leg ``source>meshcore``; omitted, ``?>meshcore``.

        Returns:
            True if queued successfully, False otherwise.
        """
        if not self._connected:
            logger.warning("Not connected to MeshCore")
            return False

        try:
            msg = CanonicalMessage(
                content=message,
                destination_address=destination,
                is_broadcast=destination is None,
                source_network=Protocol.MESHCORE.value,
            )
            # Carry the channel slot through metadata so _process_outbound
            # can route to the right slot (CanonicalMessage is protocol-
            # agnostic; channel is a MeshCore-specific concept).
            msg.metadata['channel'] = int(channel)
            if reply_ctx is not None:
                msg.metadata['dm_reply_ctx'] = dict(reply_ctx)
            if source:
                msg.metadata['leg_source'] = source
            self._send_queue.put_nowait(msg)
            # Record outbound for TUI parity — operators need to see what
            # they sent alongside what came in.
            self.record_chat_message(
                direction="tx",
                text=message,
                channel=channel if destination is None else None,
                destination=destination,
            )
            return True
        except Full:
            logger.warning("MeshCore send queue full")
            return False

    def _note_no_slot_refusal(self, msg_id, raw_channel) -> None:
        """Witness for a queued channel row refused for want of a slot."""
        with self._stats_lock:
            self.stats['meshcore_queue_row_no_slot_refused'] = (
                self.stats.get('meshcore_queue_row_no_slot_refused', 0) + 1)
        logger.warning(
            f"MeshCore queue row {msg_id!r} refused: no valid slot "
            f"(channel={raw_channel!r}); never aired on a source slot or "
            f"slot 0 (Public)")

    def queue_send(self, payload: Dict) -> bool:
        """
        Send handler for persistent queue — MeshCore destination.

        Args:
            payload: Dictionary with 'message', 'destination', 'channel' keys

        Returns:
            True if queued successfully.
        """
        message = payload.get('message', '')
        destination = payload.get('destination')

        # Backstop for a slot-less CHANNEL row written before enqueue
        # validated (validate_queue_row). It cannot tell a pre-b0b57544 row
        # whose top-level channel is the SOURCE index from a resolved one
        # (review of 39f566c1, #1; 0 such rows live at deploy). Refused
        # HERE, before the queue's mark_delivered records SENT on it, and as
        # SendRefused, not False (False read as transient: ~4 retries). The
        # queue's mark_refused counts it, logs it and records the DROPPED.
        refusal = validate_queue_row(payload)
        if refusal:
            raise SendRefused(refusal)

        if not self._connected:
            return False

        try:
            self._send_queue.put_nowait(payload)
            return True
        except Full:
            logger.warning("MeshCore send queue full (persistent)")
            return False
