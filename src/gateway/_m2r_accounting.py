"""Mesh→RNS outcome accounting — every message ends in exactly one of
delivered / dropped (operator acceptance sentence, 2026-10-18). Port of
MeshForge 56f50248 (lead repo); MeshAnchor has no session DMs, so no
session refresh. Meshtastic→RNS only: MeshCore→RNS is a separate path.

``mesh_to_rns_attempted`` counts a message once; ``mesh_to_rns_delivered`` or
``mesh_to_rns_dropped`` then counts it once — immediately when the live send
decides, or later when the persistent queue reports the terminal outcome of
the row(s) a failed send was requeued as. Between the two the message is
PENDING, which is a real state, not a missing count.

How a requeued message is followed:

* a live failure that requeues mints an ORIGIN id, stamps it on every row it
  writes (``m2r_origin``) and records the leg count here;
* ``rns`` rows: the first leg the queue reports ``delivered`` counts the
  message delivered; only when EVERY leg reports ``dropped`` is the
  message dropped;
* ``rns_xform`` rows WITH an origin are replays of an attempted message: the
  replay resolves the origin itself (``_m2r_claim_replay``), so the row's
  ``delivered`` (= replay dispatched) is not an outcome; its ``dropped``
  (expired / shed before replaying) is;
* ``rns_xform`` rows WITHOUT an origin (RNS-down requeue) were never
  attempted: the replay is the first attempt, and a row that dies
  before replaying is counted attempted + dropped here.

Counters are per process. A terminal for an origin this process never saw (a
row from before a restart, or an evicted origin) is counted
``m2r_terminal_untracked``, never folded into this process's triplet; a
resolved origin stays tracked until every leg reported, so a sibling leg
finishing is not "untracked". Tracking is bounded; an evicted origin is
witnessed (``m2r_pending_evicted``). A dedup-suppressed requeue (enqueue
returns None) counts THAT copy dropped even if the row it collided with
later delivers. Queue exits with no hook, all uncalled in production:
``retry_dead_letter`` (a counted drop would later deliver as untracked),
``clear_all``, and the ``delivered_unrecorded`` branch (row left in_progress).
"""

import logging
import uuid
from collections import OrderedDict
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

M2R_PENDING_CAP = 4096


class M2RAccountingMixin:
    """Requires: self.stats, self._stats_lock, self.health,
    self._requeue_failed_message()."""

    def _m2r_pending_map(self) -> "OrderedDict[str, Dict]":
        pend = self.__dict__.get('_m2r_pending')
        if pend is None:
            pend = self.__dict__.setdefault('_m2r_pending', OrderedDict())
        return pend

    def _m2r_bump(self, key: str, n: int = 1) -> None:
        """Caller holds self._stats_lock."""
        self.stats[key] = self.stats.get(key, 0) + n

    @staticmethod
    def _m2r_new_origin() -> str:
        return uuid.uuid4().hex

    def _m2r_track(self, origin: str, legs: int,
                   session: Optional[Tuple[str, str, object]] = None) -> None:
        """Register BEFORE the first row is enqueued: a row can reach a
        terminal (shed by its own sibling's enqueue, or drained by the worker
        thread) before enqueue() returns. ``legs`` is provisional until
        ``_m2r_settle``."""
        with self._stats_lock:
            pend = self._m2r_pending_map()
            pend[origin] = {'legs': max(1, int(legs)), 'dropped': 0,
                            'terminals': 0, 'resolved': False,
                            'settled': False, 'session': session}
            while len(pend) > M2R_PENDING_CAP:
                pend.popitem(last=False)
                self._m2r_bump('m2r_pending_evicted')

    def _m2r_settle(self, origin: str, persisted: int) -> None:
        """Fix ``legs`` to the rows actually persisted. persisted == 0: no
        row exists, the caller counts the message dropped — forget it."""
        with self._stats_lock:
            pend = self._m2r_pending_map()
            entry = pend.get(origin)
            if entry is None:
                return  # every row already reached its terminal
            if persisted <= 0:
                pend.pop(origin)
                return
            entry['legs'] = persisted
            entry['settled'] = True
            self._m2r_advance(pend, origin, entry)

    def _m2r_advance(self, pend, origin: str, entry: Dict) -> None:
        """Resolve on all-legs-dropped; forget once every leg reported.
        Caller holds self._stats_lock."""
        if not entry['resolved'] and entry['dropped'] >= entry['legs']:
            entry['resolved'] = True
            self._m2r_bump('mesh_to_rns_dropped')
        if entry['terminals'] >= entry['legs']:
            pend.pop(origin, None)

    def _m2r_claim_replay(self, origin: Optional[str]) -> bool:
        """A replay of an attempted message takes over its resolution.
        True = this process already counted the attempt."""
        if not origin:
            return False
        with self._stats_lock:
            return self._m2r_pending_map().pop(origin, None) is not None

    def _m2r_count(self, outcome: str) -> None:
        """Count one message's final outcome now."""
        with self._stats_lock:
            if outcome == 'delivered':
                self._m2r_bump('messages_mesh_to_rns')
                self._m2r_bump('mesh_to_rns_delivered')
            else:
                self._m2r_bump('mesh_to_rns_dropped')

    def _m2r_on_terminal(self, payload: Dict, outcome: str) -> None:
        """Queue terminal hook for ``rns`` rows."""
        origin = (payload or {}).get('m2r_origin')
        if not origin:
            return  # partial fan-out leg: the message was already counted
        with self._stats_lock:
            pend = self._m2r_pending_map()
            entry = pend.get(origin)
            if entry is None:
                self._m2r_bump('m2r_terminal_untracked')
                return
            entry['terminals'] += 1
            if outcome == 'delivered':
                if not entry['resolved']:
                    entry['resolved'] = True
                    self._m2r_bump('messages_mesh_to_rns')
                    self._m2r_bump('mesh_to_rns_delivered')
            else:
                entry['dropped'] += 1
            self._m2r_advance(pend, origin, entry)

    def _m2r_on_xform_terminal(self, payload: Dict, outcome: str) -> None:
        """Queue terminal hook for ``rns_xform`` rows."""
        if outcome == 'delivered':
            return  # the replay ran and counted its own outcome
        origin = ((payload or {}).get('metadata') or {}).get('m2r_origin')
        if origin:
            with self._stats_lock:
                pend = self._m2r_pending_map()
                entry = pend.get(origin)
                if entry is None:
                    self._m2r_bump('m2r_terminal_untracked')
                    return
                entry['terminals'] += 1
                entry['dropped'] += 1
                self._m2r_advance(pend, origin, entry)
            return
        # Never attempted (spill / RNS-down) and now gone: still a message.
        with self._stats_lock:
            self._m2r_bump('mesh_to_rns_attempted')
            self._m2r_bump('mesh_to_rns_dropped')

    def _m2r_rns_down(self, msg) -> bool:
        """RNS is down: queue the message for a later first attempt (the
        replay counts it). One that cannot be queued is a message lost —
        counted attempted + dropped, never vanished."""
        requeued = self._requeue_failed_message(msg, "rns")
        if requeued:
            self.health.record_message_queued_degraded()
            logger.debug("Mesh→RNS: RNS subsystem down, message queued")
        else:
            with self._stats_lock:
                self._m2r_bump('mesh_to_rns_attempted')
                self._m2r_bump('mesh_to_rns_dropped')
            logger.warning("Mesh→RNS: RNS down and the message could not be "
                           "queued — dropped")
        return requeued
