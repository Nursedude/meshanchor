"""RNS event handlers for RNSMeshtasticBridge — LXMF receive + announce.

Extracted from rns_bridge.py (MF025 1,500-line cap) along the seam MeshForge
already runs (its gateway/bridge_rns_events_mixin.py). Bodies moved VERBATIM
except for the 2026-09-26 single-capture fix in _on_rns_announce; module
names are bound from gateway.rns_bridge at call time so the existing
patch("gateway.rns_bridge.<name>") test seams still apply.
"""

import logging
import threading
import time
from pathlib import Path

from utils.paths import get_real_user_home

from .base_handler import UNVERIFIED_MARK

logger = logging.getLogger(__name__)

#: Monotonic clock for the path-nudge rate limit — a module name so a test
#: can pin it without patching the SHARED ``time`` module.
_monotonic = time.monotonic
#: At most one source-unknown path request per member hash per this many
#: seconds: a forger spamming claims for a member hash must not make us storm.
IDENTITY_PATH_NUDGE_S = 60.0
#: LXMF's unverified_reason for "source never announced" — the ONLY reason a
#: path request can cure (an invalid signature is a forgery, not a stale cache).
_SOURCE_UNKNOWN = 0x01
#: Seconds the warm-up waits for rnsd's path responses before re-measuring.
IDENTITY_WARMUP_SETTLE_S = 5.0
#: Single-flight: one warm-up at a time however often RNS reconnects.
_WARMUP_LOCK = threading.Lock()


def _spawn(fn, *args) -> None:
    """Daemon thread seam (tests run it inline). A path request is an rnsd
    call with no bounded wrapper here, so it must never run on the LXMF
    delivery thread (#72: a wedged rnsd would stall ingress)."""
    threading.Thread(target=fn, args=args, name="rns-identity-nudge",
                     daemon=True).start()

#: One ledger object per process: get_status() and the LXMF delivery thread
#: may race to the first construction (MF twin, non-author review 2026-10-07).
_RNS_INGRESS_INIT_LOCK = threading.Lock()


def default_ma_ledger_path() -> Path:
    """MeshAnchor's data dir — writable by the daemon unit (ReadWritePaths
    covers /home). The shared policy module's own default names MeshForge's
    dir, so MeshAnchor always passes this one."""
    from .rns_ingress_policy import LEDGER_FILENAME
    return get_real_user_home() / ".local" / "share" / "meshanchor" / LEDGER_FILENAME



class BridgeRnsEventsMixin:
    """Mixin: _on_lxmf_receive / _on_rns_announce for RNSMeshtasticBridge."""

    #: The RNS→MeshCore ingress methods a host must carry (tests bind these).
    INGRESS_METHODS = ("_rns_ingress_ledger", "rns_ingress_posture",
                       "rns_ingress_stamp", "_rns_ingress_admits")

    def _on_lxmf_receive(self, message):
        """Handle incoming LXMF message"""
        # Module names resolve off the hub at call time so patch("gateway.rns_bridge.X")
        # keeps working for this extracted handler (MeshForge's shape).
        from . import rns_bridge as _h
        (BridgedMessage, Full, HAS_RNS_SNIFFER, RNSPacketInfo, RNSPacketType, UnifiedNode, get_rns_sniffer, logger) = (
            getattr(_h, "BridgedMessage", None), getattr(_h, "Full", None), getattr(_h, "HAS_RNS_SNIFFER", None), getattr(_h, "RNSPacketInfo", None), getattr(_h, "RNSPacketType", None), getattr(_h, "UnifiedNode", None), getattr(_h, "get_rns_sniffer", None), getattr(_h, "logger", None))
        try:
            source_hash = message.source_hash
            # The source hash is a CLAIM until LXMF validated its signature
            # (forged → SIGNATURE_INVALID, never-announced → SOURCE_UNKNOWN).
            # Consumers that act on WHO sent this read `verified`, never the
            # field (non-author pass on the MF 10-07 arc, 2026-10-15).
            verified = getattr(message, "signature_validated", False) is True
            if (not verified and getattr(message, "unverified_reason", None)
                    == _SOURCE_UNKNOWN):
                try:
                    self._rns_identity_path_nudge(source_hash.hex())
                except Exception as e:  # noqa: BLE001 — witness, never decisive
                    logger.debug(f"rns identity path nudge failed: {e}")

            # Update node info — a forged claim is not evidence the node exists
            if verified:
                node = UnifiedNode.from_rns(source_hash)
                self.node_tracker.add_node(node)

            # Capture LXMF message for traffic inspection
            if HAS_RNS_SNIFFER:
                try:
                    sniffer = get_rns_sniffer()
                    if sniffer and sniffer._running:
                        # LXMessage.content can be either bytes (binary LXMF
                        # payload) or str — encode only when we got text.
                        # (MeshForge #1162: 'bytes'.encode() raised, dropping
                        # the capture; delivery itself was unaffected.)
                        raw_content = message.content or b''
                        content_bytes = (
                            raw_content.encode('utf-8')
                            if isinstance(raw_content, str)
                            else raw_content
                        )
                        packet_info = RNSPacketInfo(
                            packet_type=RNSPacketType.DATA,
                            source_hash=source_hash,
                            direction="inbound",
                            payload=content_bytes,
                            payload_size=len(content_bytes),
                            announce_aspect="lxmf.delivery",
                        )
                        sniffer._store_packet(packet_info)
                except Exception as e:
                    logger.debug(f"RNS sniffer LXMF capture error: {e}")

            # Decode LXMF bytes->str up front so stored records and the
            # BridgedMessage carry clean text, not Python bytes reprs.
            # Without this, messages.db logs "[b'Meshtastic'] b'...'".
            content_str = (
                message.content.decode("utf-8", errors="replace")
                if isinstance(message.content, (bytes, bytearray))
                else (message.content or "")
            )
            title_str = (
                message.title.decode("utf-8", errors="replace")
                if isinstance(message.title, (bytes, bytearray))
                else (message.title or "")
            )

            msg = BridgedMessage(
                source_network="rns",
                source_id=source_hash.hex(),
                destination_id=None,
                # an unverified source travels MARKED to RF, the event bus
                # and the TUI (non-author read of MF 11388546, 2026-10-15)
                content=(content_str if verified
                         else UNVERIFIED_MARK + content_str),
                title=title_str,
                metadata={
                    'lxmf_stamp': message.stamp,
                    'source_verified': verified,
                }
            )

            # Store incoming message for UI/history
            try:
                from commands import messaging
                # Combine title and content for RNS messages (decoded)
                content = content_str
                if title_str:
                    content = f"[{title_str}] {content}"
                if not verified:
                    content = f"[unverified sender] {content}"
                messaging.store_incoming(
                    from_id=source_hash.hex(),
                    content=content,
                    network="rns",
                    to_id=None,  # LXMF doesn't have destination in received messages
                )
            except Exception as e:
                logger.debug(f"Could not store incoming RNS message: {e}")

            # RNS→MeshCore ingress policy (MF port, 2026-10-15): WHO may reach
            # our radios through the bridge. BEFORE the queue AND the re-emit
            # hook — a refused sender has no other way onto RF. Still
            # notified, so the TUI shows it. See gateway/rns_ingress_policy.py.
            if not self._rns_ingress_admits(
                    source_hash.hex(),
                    signature_validated=getattr(message, 'signature_validated', None),
                    unverified_reason=getattr(message, 'unverified_reason', None)):
                self._notify_message(msg)
                return

            # Queue for bridging if enabled (non-blocking to prevent deadlock)
            if self._router.should_bridge(msg):
                try:
                    self._rns_to_mesh_queue.put_nowait(msg)
                except Full:
                    logger.warning("RNS→Mesh queue full, dropping message")
                    with self._stats_lock:
                        self.stats['errors'] += 1

            # Notify callbacks
            self._notify_message(msg)

            # LXMF→MeshCore re-emit hook. Only acts when the operator
            # opted in via meshtastic_reemit.enabled=True AND the LXMF
            # source_hash matches one of the configured
            # MeshtasticBroadcastBridge identities. Cheap no-op
            # otherwise. Wrapped so a bridge bug can't take down the
            # gateway's LXMF RX path.
            # Its allowlist keys on the hash, so only a VALIDATED signature
            # may reach it: a forged claim of the broadcast-bridge identity
            # was re-emitted onto MeshCore RF.
            if self._meshtastic_reemit and verified:
                try:
                    self._meshtastic_reemit.on_lxmf_message(
                        source_hash, content_str,
                    )
                except Exception as e:
                    logger.debug(f"meshtastic_reemit hook error: {e}")

        except Exception as e:
            logger.error(f"Error processing LXMF message: {e}")

    # ── RNS→MeshCore ingress policy (MeshForge port) ─────────────────────
    def _rns_ingress_ledger(self):
        """Lazily-built, process-wide ledger of unlisted senders."""
        led = getattr(self, "_rns_ingress_ledger_obj", None)
        if led is None:
            with _RNS_INGRESS_INIT_LOCK:
                led = getattr(self, "_rns_ingress_ledger_obj", None)
                if led is None:
                    from .rns_ingress_policy import IngressLedger
                    path = getattr(self, "_rns_ingress_ledger_path", None)
                    led = IngressLedger(path or default_ma_ledger_path())
                    self._rns_ingress_ledger_obj = led
        return led

    # ── identity warm-up (2026-10-16; MF 2026-10-08 port, review I4) ──
    # After a daemon restart this process cannot recall its LXMF sources
    # until each re-announces (≤10 min), so LXMF delivers them UNVERIFIED and
    # the re-emit (verified-only) drops them: live 01:13:36→01:17:26, every bot
    # reply missed the MeshCore channel. Ask rnsd now, then RE-MEASURE.

    def _rns_identity_warmup_members(self) -> list:
        """Re-emit sources ∪ listed identities — ALWAYS, whatever the ingress
        policy: the re-emit needs a VERIFIED source even while ingress is
        OPEN (MF's warm-up returns early on open; MA's posture is open)."""
        members = set()
        reemit = getattr(self, "_meshtastic_reemit", None)
        if reemit is not None:
            members |= {h.lower() for h in
                        (getattr(reemit, "_allowed_identities", None) or ())}
        try:
            members |= {h.lower() for h in
                        (self.rns_ingress_posture().get("identities") or ())}
        except Exception as e:  # noqa: BLE001
            logger.debug(f"rns identity warm-up posture: {e}")
        return sorted(members)

    def _rns_recall_identity(self, dest: bytes):
        """The question LXMF itself asks (``Identity.recall(h, _no_use=True)``):
        a local dict read, no RPC to rnsd."""
        import RNS
        return RNS.Identity.recall(dest, _no_use=True)

    def _rns_request_path(self, dest: bytes) -> str:
        """``sent`` | ``blocked`` (tx_guard armed — deliberate, not a fault)."""
        import RNS
        from utils.tx_guard import TransmitBlocked, assert_rns_tx_allowed
        try:
            assert_rns_tx_allowed(kind="rns_path_request",
                                  detail="LXMF source identity warm-up")
        except TransmitBlocked:
            return "blocked"
        RNS.Transport.request_path(dest)
        return "sent"

    def rns_identity_warmup(self, *, settle_s: float = IDENTITY_WARMUP_SETTLE_S) -> dict:
        """At RNS connect: a path request for every member this process cannot
        recall, wait ``settle_s``, then RE-MEASURE — "request sent" is not the
        END (rnsd ignores a request whose announce cache entry is gone).
        Single-flight; never raises; {} when there is nothing to warm."""
        if not _WARMUP_LOCK.acquire(blocking=False):
            return {}
        try:
            return self._rns_identity_warmup_once(settle_s)
        finally:
            _WARMUP_LOCK.release()

    def _rns_identity_warmup_once(self, settle_s: float) -> dict:
        members = self._rns_identity_warmup_members()
        if not members:
            return {}

        def recallable(h: str) -> bool:
            try:
                return self._rns_recall_identity(bytes.fromhex(h)) is not None
            except Exception as e:  # noqa: BLE001
                logger.debug(f"rns identity recall {h[:8]}: {e}")
                return False

        missing = [h for h in members if not recallable(h)]
        requested = blocked = failed = 0
        for h in missing:
            try:
                r = self._rns_request_path(bytes.fromhex(h))
                requested += r == "sent"
                blocked += r == "blocked"
            except Exception as e:  # noqa: BLE001 — counted, never raised
                failed += 1
                logger.debug(f"rns identity warm-up {h[:8]}: {e}")
        if requested and settle_s > 0:
            ev = getattr(self, "_stop_event", None)
            if ev is not None:
                ev.wait(settle_s)
        still = [h for h in missing if not recallable(h)] if requested else missing
        after = len(members) - len(still)
        logger.info(
            "RNS identity warm-up (re-emit sources + listed): %d/%d "
            "recallable in-process before, %d/%d after %d path request(s)"
            "%s%s%s", len(members) - len(missing), len(members), after,
            len(members), requested,
            f"; {blocked} blocked by tx_guard" if blocked else "",
            f"; {failed} FAILED" if failed else "",
            f"; still unknown: {','.join(h[:8] for h in still)}" if still else "")
        return {"members": len(members), "known": len(members) - len(missing),
                "requested": requested, "blocked": blocked, "failed": failed,
                "after": after}

    def _rns_identity_path_nudge(self, source_hex: str) -> None:
        """One path request per MEMBER hash per IDENTITY_PATH_NUDGE_S, off the
        delivery thread. A stranger is never nudged (no storm on forgeries)."""
        h = source_hex.lower()
        if h not in set(self._rns_identity_warmup_members()):
            return
        last = getattr(self, "_rns_identity_nudged", None)
        if last is None:
            last = self._rns_identity_nudged = {}
        now = _monotonic()
        if h in last and now - last[h] < IDENTITY_PATH_NUDGE_S:
            return
        last[h] = now
        _spawn(self._rns_request_path, bytes.fromhex(h))

    def rns_ingress_posture(self) -> dict:
        """The declared list + the policy in force, for status surfaces."""
        from .rns_ingress_policy import effective_policy
        rns_cfg = getattr(getattr(self, "config", None), "rns", None)
        try:
            identities = list(rns_cfg.get_bridge_source_identities())
        except Exception:  # noqa: BLE001 — a mocked/absent section is OPEN
            identities = []
        declared = getattr(rns_cfg, "bridge_source_policy", None)
        policy = effective_policy(identities, declared if isinstance(declared, str) else None)
        return {"policy": policy, "listed": len(identities), "identities": identities}

    def rns_ingress_stamp(self) -> None:
        """Write the posture into the ledger at start, so a declared gateway
        with ZERO unlisted senders is distinguishable from no gateway. Only
        when a list is declared (or a ledger exists). Never raises."""
        try:
            posture = self.rns_ingress_posture()
            led = self._rns_ingress_ledger()
            if posture["policy"] != "open" or led.path.exists():
                led.stamp(policy=posture["policy"], listed=posture["listed"],
                          identities=posture["identities"])
        except Exception as e:  # noqa: BLE001
            logger.debug(f"rns ingress stamp skipped: {e}")

    def _rns_ingress_admits(self, source_hex: str, *, signature_validated=None,
                            unverified_reason=None) -> bool:
        """True when ``source_hex`` may be bridged onto RF. Never raises.

        Membership counts only with ``signature_validated is True`` — a
        claimed hash is not an identity. A broken POLICY admits as OPEN and
        says so; broken STATE (ledger) can never open the bridge (MF twin)."""
        try:
            from .rns_ingress_policy import (
                POLICY_ENFORCE, POLICY_OPEN, VERDICT_LISTED, VERDICT_UNVERIFIED, verdict)
            posture = self.rns_ingress_posture()
            policy = posture["policy"]
            if policy == POLICY_OPEN:
                return True
            v = verdict(source_hex, posture["identities"], (),
                        signature_validated=signature_validated)
            if v == VERDICT_LISTED:
                try:
                    self._rns_ingress_ledger().note_listed(source_hex)
                except Exception as e:  # noqa: BLE001 — witness, never decisive
                    logger.debug(f"rns ingress use note failed: {e}")
                return True
            unverified = v == VERDICT_UNVERIFIED
            refused = policy == POLICY_ENFORCE
        except Exception as e:  # noqa: BLE001
            logger.warning(f"rns ingress policy error ({e}); admitting "
                           f"{source_hex[:8]} as if OPEN")
            return True
        # The DECISION is made; everything below is witness-keeping.
        with self._stats_lock:
            self.stats['rns_ingress_unlisted'] = self.stats.get('rns_ingress_unlisted', 0) + 1
            if refused:
                self.stats['rns_to_mesh_refused_unlisted'] = (
                    self.stats.get('rns_to_mesh_refused_unlisted', 0) + 1)
        ent: dict = {}
        try:
            ent = self._rns_ingress_ledger().record(
                source_hex, policy=policy, refused=refused, label="",
                unverified=unverified)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"rns ingress ledger record failed ({e}); the "
                           f"journal line below is the only witness")
        if unverified:
            from .rns_ingress_policy import unverified_words
            why = (f"CLAIMS a listed identity but {unverified_words(unverified_reason)}"
                   f" — treated as unlisted")
        else:
            why = "is not in bridge_source_identities"
        logger.info(
            "RNS ingress %s: sender %s %s (policy=%s, seen %d×%s) — %s",
            "REFUSED" if refused else "unlisted", source_hex.lower(), why, policy,
            ent.get("seen", 1), f", refused {ent.get('refused', 0)}×" if refused else "",
            "not bridged onto MeshCore RF" if refused else
            "bridged anyway (observe); add it to the list or enforce")
        return not refused

    def _on_rns_announce(self, dest_hash, announced_identity, app_data):
        """Handle RNS announce for node discovery"""
        # Module names resolve off the hub at call time so patch("gateway.rns_bridge.X")
        # keeps working for this extracted handler (MeshForge's shape).
        from . import rns_bridge as _h
        (BridgedMessage, Full, HAS_RNS_SNIFFER, RNSPacketInfo, RNSPacketType, UnifiedNode, get_rns_sniffer, logger) = (
            getattr(_h, "BridgedMessage", None), getattr(_h, "Full", None), getattr(_h, "HAS_RNS_SNIFFER", None), getattr(_h, "RNSPacketInfo", None), getattr(_h, "RNSPacketType", None), getattr(_h, "UnifiedNode", None), getattr(_h, "get_rns_sniffer", None), getattr(_h, "logger", None))
        try:
            # Capture announce packet for traffic inspection
            if HAS_RNS_SNIFFER:
                try:
                    import RNS
                    sniffer = get_rns_sniffer()
                    # The sniffer registers its OWN announce handler (aspect
                    # filter None = every announce); capturing here too stored
                    # each announce TWICE (measured on meshanchor-server
                    # 2026-09-26: 180 same-second repeats in 400 rows). Capture
                    # here only as the fallback when the sniffer has no hooks
                    # (port of MeshForge 0f0502f4).
                    if (sniffer and sniffer._running
                            and not getattr(sniffer, "_hooks_installed", False)):
                        packet_info = RNSPacketInfo(
                            packet_type=RNSPacketType.ANNOUNCE,
                            destination_hash=dest_hash,
                            direction="inbound",
                            announce_app_data=app_data,
                            announce_aspect="lxmf.delivery",
                        )
                        # Get identity hash if available
                        if announced_identity:
                            try:
                                packet_info.source_hash = announced_identity.hash
                                packet_info.announce_identity = announced_identity.hash
                            except Exception:
                                pass
                        # Get hop count
                        try:
                            if RNS.Transport.has_path(dest_hash):
                                hops = RNS.Transport.hops_to(dest_hash)
                                packet_info.hops = hops if hops is not None else 0
                        except Exception:
                            pass
                        sniffer._store_packet(packet_info)
                except Exception as e:
                    logger.debug(f"RNS sniffer capture error: {e}")

            node = UnifiedNode.from_rns(dest_hash, app_data=app_data)
            self.node_tracker.add_node(node)
            logger.debug(f"Discovered RNS node: {dest_hash.hex()[:8]}")
        except Exception as e:
            logger.error(f"Error processing RNS announce: {e}")

    # Routing delegated to MessageRouter (see gateway/message_routing.py)
