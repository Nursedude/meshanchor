"""CS-1 MeshCore observer wiring for MeshCoreHandler.

Off by default (``meshcore.observer_enabled``). A session wired while off is
never subscribed (structural off). When on, every companion ``RX_LOG_DATA``
event is translated by ``meshcore_observer.build_packet_message`` and handed
to a publisher (the local broker); every outcome lands in an
``ObserverWitness``. A publish failure is COUNTED, never raised into the radio
event loop: the companion is shared with chat and the bridge.

ONE predicate for state AND behaviour (v3). ``_observer_state_for(session)``
is ``derive_state`` over inputs read LIVE — ``enabled`` from the config,
``radio`` = connected AND that session is the current one — plus the
``(session, setup, error)`` record bound to that session by identity. The
snapshot asks it about ``self._meshcore``; each callback is bound to the
session it subscribed on and publishes only while that session's state is a
runtime state — otherwise the packet is dropped before translation and
counted in ``dropped_not_live[<cause>]`` (``stale_session`` / ``disabled`` /
the current session's state).

``_init_observer`` writes the setup record at its END; on a session already
wired it subscribes nothing more. A disable KEEPS that session's subscription
and resets the witness in place (its callback then counts every packet as
``disabled``); the witness survives reconnects.

The journal is a projection of the DERIVED STATE (v4/v5): a line whenever
(state, detail) differs from the last LOGGED key — including the init
``enabled`` / ``disabled`` lines, which record their key — rate-limited per key
(``LOG_WINDOW_S``), a held-back change counted once in ``log_lines_suppressed``
and emitted on the first packet after its window reopens. The journal can
disagree with the state for at most one window, never forever.

Host attributes used: self._meshcore, self._connected, self._subscriptions,
self.config.meshcore
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from .meshcore_observer import (JOURNAL_DISABLED, JOURNAL_HEALTHY, RUNTIME_STATES,
                                ObserverWitness, Refused,
                                build_packet_message, derive_state,
                                disabled_snapshot, is_valid_iata,
                                make_local_publisher, observer_enabled)

logger = logging.getLogger(__name__)

_BAD_OUTCOMES = {
    "refused": "MeshCore observer refusing packets (%s) — nothing reaches the broker",
    "publish_failed": "MeshCore observer publish failing (%s)",
    "internal_error": "MeshCore observer internal error (%s) — a bug, counted",
}


class MeshCoreObserverMixin:
    """RX_LOG_DATA -> local broker, with a witness."""

    _observer_witness = None
    _observer_publisher = None
    _observer_iata = ""
    _observer_setup = None     # (session, setup, error) — ONE atomic record

    def _observer_cfg(self) -> Any:
        return getattr(getattr(self, "config", None), "meshcore", None)

    def _observer_note_unwired(self, mode: str) -> None:
        """A connect path that never wires the observer (simulation): never
        silently inert (same class as supervisor mode, r1 MED-1 / r3 F2)."""
        if observer_enabled(self._observer_cfg()):
            logger.warning("MeshCore observer is enabled but %s does not carry "
                           "RX_LOG_DATA: nothing is observed", mode)

    def _init_observer(self, event_type_cls: Any, publisher: Optional[Any] = None) -> None:
        cfg = self._observer_cfg()
        session = self._meshcore
        if not observer_enabled(cfg):
            # v4 (r4 L3): KEEP this session's subscription + setup record and
            # reset the witness IN PLACE. Its callback drops every packet as
            # `disabled` (counted), and a re-enable on the same session is a
            # no-op wiring — never a second subscription. A NEW session made
            # while disabled is never subscribed (structural off).
            if self._observer_witness is not None:
                self._observer_witness.reset()
                self._observer_witness.journal_logged(JOURNAL_DISABLED)
            logger.info("MeshCore observer disabled (meshcore.observer_enabled = false)")
            return
        self._observer_iata = str(getattr(cfg, "observer_iata", "") or "")
        if not is_valid_iata(self._observer_iata):
            logger.warning("MeshCore observer: observer_iata %r is not a 3-letter "
                           "upper-case IATA code — every packet will be refused "
                           "(bad_iata)", self._observer_iata)
        rec = self._observer_setup
        if rec is not None and session is not None and rec[0] is session and rec[1] == "ok":
            # already wired to THIS session: never subscribe twice — but a
            # re-enable still SAYS so, and the IATA above was re-read (r5 LOW-1)
            self._observer_witness.journal_logged(JOURNAL_HEALTHY)
            logger.info("MeshCore observer enabled (already wired): RX_LOG_DATA -> "
                        "local broker (IATA %r)", self._observer_iata)
            return
        setup, error = "ok", None
        # _subscribe_events runs on EVERY (re)connect: build the client once.
        if publisher is not None:
            self._observer_publisher = publisher
        elif self._observer_publisher is None:
            try:
                self._observer_publisher = make_local_publisher()
            except Exception as e:  # e.g. paho 1.x: no CallbackAPIVersion
                setup, error = "client_raised", f"{type(e).__name__}: {e}"
                logger.warning("MeshCore observer: MQTT client failed: %s", e)
        if setup == "ok" and self._observer_publisher is None:
            setup = "no_client"
            logger.warning("MeshCore observer enabled but paho-mqtt is not installed")
        if setup == "ok":
            if self._observer_witness is None:
                self._observer_witness = ObserverWitness(clock=time.monotonic)
            try:
                sub = session.subscribe(event_type_cls.RX_LOG_DATA,
                                        self._observer_callback_for(session))
            except Exception as e:  # a blind observer is LOUD, never silent
                setup, error = "subscribe_raised", f"{type(e).__name__}: {e}"
                logger.warning("MeshCore observer subscribe failed: %s", e)
            else:
                self._subscriptions.append(sub)
                self._observer_witness.journal_logged(JOURNAL_HEALTHY)
                logger.info("MeshCore observer enabled: RX_LOG_DATA -> local broker "
                            "(IATA %r)", self._observer_iata)
        self._observer_setup = (session, setup, error)

    def _observer_state_for(self, session: Any, last_outcome: Optional[str] = None) -> str:
        """THE predicate: the snapshot and every callback ask this, nothing else."""
        if not observer_enabled(self._observer_cfg()):
            return "disabled"
        current = session is not None and session is self._meshcore
        radio = "up" if getattr(self, "_connected", False) and current else "down"
        rec = self._observer_setup
        # no `session is not None` here: a None session is already radio=down,
        # which precedes every setup row (so the check would be dead code)
        bound = rec is not None and rec[0] is session
        if last_outcome is None:
            witness = self._observer_witness
            last_outcome = witness.last_outcome if witness is not None else "none"
        return derive_state(enabled=True, mode="handler", radio=radio,
                            setup=rec[1] if bound else "not_attempted",
                            last_outcome=last_outcome)

    def _observer_callback_for(self, session: Any):
        async def _on_rx_log_for_session(event: Any) -> None:
            await self._on_rx_log(session, event)
        return _on_rx_log_for_session

    def _observer_journal(self, witness: Any) -> None:
        """Project the CURRENT derived state into the journal (v4, r4 M1/L1/I1):
        one line whenever (state, detail) differs from the last logged one,
        rate-limited per key by the witness, a held-back change emitted on the
        first packet after its window reopens."""
        snap = witness.snapshot()
        state = self._observer_state_for(self._meshcore, last_outcome=snap["last_outcome"])
        if state in ("never", "ok"):
            key, line = JOURNAL_HEALTHY, None
        elif state == "refusing":
            key = f"refusing:{snap['last_refused']}"
            line = (logging.WARNING, _BAD_OUTCOMES["refused"], snap["last_refused"])
        elif state == "failing":
            outcome = snap["last_outcome"]
            err = snap["last_error"] or ""
            key = f"failing:{outcome}:{err.split(':', 1)[0]}"
            line = (logging.WARNING, _BAD_OUTCOMES[outcome], err)
        elif state == "disabled":
            key, line = JOURNAL_DISABLED, (logging.INFO, "MeshCore observer disabled: "
                                           "dropping every packet (%s so far)", None)
        else:
            key, line = f"{state}:", (logging.WARNING, "MeshCore observer not live (%s): "
                                      "dropping packets (%s so far)", state)
        if not witness.journal_owes(key):
            return
        if line is None:
            logger.info("MeshCore observer recovered: queuing to the broker again")
            return
        level, fmt, detail = line
        # the count is every not-live drop the line stands for — by CAUSE a
        # late stale-session callback is `stale_session`, not `no_radio` (r5 LOW-3)
        dropped = sum(snap["dropped_not_live"].values())
        if state == "disabled":
            logger.log(level, fmt, dropped)
        elif state in ("refusing", "failing"):
            logger.log(level, fmt, detail)
        else:
            logger.log(level, fmt, detail, dropped)

    def _observer_drop_cause(self, session: Any, state: str) -> str:
        if state == "disabled":
            return "disabled"
        if session is not self._meshcore:
            return "stale_session"   # a session the handler moved past (r4 L2)
        return state

    async def _on_rx_log(self, session: Any, event: Any) -> None:
        witness = self._observer_witness   # set before any subscribe; never cleared
        try:
            await self._on_rx_log_counted(witness, session, event)
        finally:
            self._observer_journal(witness)

    async def _on_rx_log_counted(self, witness: Any, session: Any, event: Any) -> None:
        state = self._observer_state_for(session)
        if state not in RUNTIME_STATES:
            witness.record_dropped(self._observer_drop_cause(session, state))
            return
        try:
            info = getattr(session, "self_info", None)
            if not isinstance(info, dict):
                info = {}   # no own key -> refused below as bad_observer_key
            key = str(info.get("public_key", "") or "").upper()
            result = build_packet_message(
                getattr(event, "payload", None), iata=self._observer_iata,
                observer_key=key, origin=info.get("name"))
        except Exception as e:  # our bug: counted, never raised into the radio loop
            witness.record_internal_error(e)
            return
        if isinstance(result, Refused):
            witness.record_refused(result)
            return
        try:
            self._observer_publisher.publish(result.topic, result.body)
        except Exception as e:  # counted, never raised into the radio loop
            witness.record_publish_failed(result, e)
            return
        witness.record_queued(result)

    def observer_snapshot(self) -> Dict[str, Any]:
        cfg = self._observer_cfg()
        if not observer_enabled(cfg):
            return disabled_snapshot(cfg)
        session = self._meshcore
        witness = self._observer_witness
        snap = witness.snapshot() if witness is not None else {"last_outcome": "none"}
        state = self._observer_state_for(session, last_outcome=snap["last_outcome"])
        if state in RUNTIME_STATES:
            snap["state"] = state
            return snap
        out: Dict[str, Any] = {"state": state}
        rec = self._observer_setup
        if rec is not None and session is not None and rec[0] is session and rec[2] is not None:
            out["last_error"] = rec[2]
        if snap.get("dropped_not_live"):
            out["dropped_not_live"] = snap["dropped_not_live"]
        return out
