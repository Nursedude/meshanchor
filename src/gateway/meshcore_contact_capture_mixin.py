"""Allowlist-driven MeshCore contact capture (2026-08-29).

Why this exists: the directed-DM reply leg (``meshcore_dm_reply.py``) can only
deliver to contacts the companion radio has STORED — and MeshCore
public-channel senders are never stored automatically, so the first live
positive-path test ('@p1 good day', 2026-08-29 08:02 HST) died on
``contact not found`` for a name that was plainly on the air. Hand-adding
contacts over the serial link is firmware state in one radio on one box:
unversioned, unreproducible, invisible to the repo — it fails the
reliable/scalable/portable bar (operator, 2026-08-29).

This mixin is the portable shape: a small config allowlist
(``meshcore.repliable_contacts`` — adv_names or pubkey prefixes), and when a
listed peer's advert reaches the radio as a pending contact
(``EventType.NEW_CONTACT``), the daemon stores it via
``commands.add_contact`` through its own connection. Policy lives in config;
the contact DB repopulates itself from real adverts on any rebuilt box; the
DB grows only with declared intent — never with ambient public-mesh noise
(no firmware contact-cap blowout, the risk that ruled out blanket
``set_manual_add_contacts(False)``).

Honest-failure notes: an EMPTY allowlist subscribes nothing (inert by
design, logged once at debug). Every capture attempt leaves a witness —
``meshcore_contact_captured`` or ``meshcore_contact_capture_failed`` — and a
failed add logs at WARNING with the peer named. A pending contact that
matches nothing is silence on purpose: on a public mesh that is the common
case, not an error.

Expects on the host class (MeshCoreHandler):
- self._meshcore (meshcore_py MeshCore) / self._subscriptions
- self.config.meshcore.repliable_contacts
- self.stats / self._stats_lock
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MeshCoreContactCaptureMixin:
    """NEW_CONTACT subscription + allowlisted add_contact capture."""

    def _init_contact_capture(self, event_type_cls: Any) -> None:
        """Wire contact capture: subscribe NEW_CONTACT and sweep the lib's
        already-pending contacts (adverts heard before we subscribed).

        Called from ``_subscribe_events`` with the resolved ``EventType``
        class so this module never imports meshcore_py directly (same
        pattern as the host's other subscriptions).
        """
        if not self._capture_allowlist():
            logger.debug(
                "MeshCore contact capture inert — "
                "meshcore.repliable_contacts is empty")
            return
        try:
            sub = self._meshcore.subscribe(
                event_type_cls.NEW_CONTACT, self._on_new_contact)
            self._subscriptions.append(sub)
        except Exception as e:
            # A failed subscription means listed peers can never be
            # captured — that is a loud condition, not a debug line.
            logger.warning(f"MeshCore contact-capture subscribe failed: {e}")
            return
        try:
            asyncio.ensure_future(self._sweep_pending_contacts())
        except RuntimeError:
            # No running loop (sync/test context) — the NEW_CONTACT
            # subscription still stands; only the backlog sweep is skipped.
            logger.debug("contact-capture pending sweep skipped (no loop)")

    def _capture_allowlist(self) -> List[str]:
        cfg = getattr(self.config, 'meshcore', None)
        entries = getattr(cfg, 'repliable_contacts', None) or []
        return [str(e).strip() for e in entries if str(e).strip()]

    @staticmethod
    def _capture_match(contact: Dict[str, Any],
                       entries: List[str]) -> Optional[str]:
        """The allowlist entry this pending contact satisfies, or None.

        Same semantics as ``_find_contact`` (exact adv_name, or substring
        of the pubkey hex) so an address that can be captured is exactly an
        address the DM leg can later resolve.
        """
        name = str(contact.get('adv_name', '') or '')
        pk = contact.get('public_key', '')
        pk_hex = pk.hex() if isinstance(pk, bytes) else str(pk or '')
        for entry in entries:
            if entry == name or (pk_hex and entry in pk_hex):
                return entry
        return None

    async def _on_new_contact(self, event: Any) -> None:
        payload = getattr(event, 'payload', None)
        if isinstance(payload, dict):
            await self._maybe_capture_contact(payload)

    async def _sweep_pending_contacts(self) -> None:
        pending = getattr(self._meshcore, 'pending_contacts', None) or {}
        for contact in list(pending.values()):
            if isinstance(contact, dict):
                await self._maybe_capture_contact(contact)

    async def _maybe_capture_contact(self, contact: Dict[str, Any]) -> None:
        """Store one pending contact iff it is allowlisted and not stored."""
        entry = self._capture_match(contact, self._capture_allowlist())
        if entry is None:
            return  # not listed — the common case on a public mesh
        pk = contact.get('public_key', '')
        pk_hex = pk.hex() if isinstance(pk, bytes) else str(pk or '')
        stored = getattr(self._meshcore, 'contacts', None) or {}
        if pk_hex and pk_hex in stored:
            return  # already a stored contact — nothing to do
        name = contact.get('adv_name', '') or f"MC-{pk_hex[:6]}"
        try:
            evt = await self._meshcore.commands.add_contact(contact)
            evt_type = str(getattr(evt, 'type', '')).lower()
            if 'error' in evt_type:
                raise RuntimeError(f"radio returned {evt_type}")
        except Exception as e:
            with self._stats_lock:
                self.stats.setdefault('meshcore_contact_capture_failed', 0)
                self.stats['meshcore_contact_capture_failed'] += 1
            logger.warning(
                f"MeshCore contact capture FAILED for '{name}' "
                f"({pk_hex[:12]}…, allowlist entry '{entry}'): {e}")
            return
        with self._stats_lock:
            self.stats.setdefault('meshcore_contact_captured', 0)
            self.stats['meshcore_contact_captured'] += 1
        logger.info(
            f"MeshCore contact captured: '{name}' ({pk_hex[:12]}…) "
            f"matched repliable_contacts entry '{entry}' — now DM-able")
