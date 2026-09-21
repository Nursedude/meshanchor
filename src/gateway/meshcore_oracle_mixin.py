"""Mesh-oracle wiring for the MeshCore handler — handler-side mixin.

Split out of ``meshcore_handler.py`` on 2026-09-20 (the 1,500-line cap,
MF025) when the DM reply leg was fixed; same pattern as
``MeshCoreRadioOpsMixin`` / ``MeshCoreDmAckMixin``. Builds the read-only
responder that ``_on_contact_message`` (DM leg) and ``_on_channel_message``
(channel leg) call — see those hooks for the gate each leg applies.

Expects on the host class:
- self.config.meshcore (bridge_target_channel)
- self.send_text(text, destination=, channel=)
"""

import logging

logger = logging.getLogger(__name__)


def _is_wire_prefix(value: str) -> bool:
    """True when ``value`` has the shape of a MeshCore DM target as the wire
    carries it: the even-length hex ``pubkey_prefix`` meshcore_py's reader.py
    sets on CONTACT_MSG_RECV (6 bytes = 12 hex today). A display name parsed
    out of a channel message's text header never has it."""
    v = str(value or "")
    return (len(v) >= 8 and len(v) % 2 == 0
            and all(c in "0123456789abcdefABCDEF" for c in v))


class MeshCoreOracleMixin:
    """Construct the MeshCore oracle responder (opt-in, default OFF)."""
    def _build_meshcore_oracle_responder(self):
        """Construct the read-only MeshCore oracle responder, or None if disabled.

        Default OFF (opt-in via MESHANCHOR_ORACLE_ENABLED) — the env is checked
        BEFORE importing the oracle so a disabled daemon pays no import cost.
        Access is additive: a known sender (MESHANCHOR_ORACLE_MESHCORE_ALLOWLIST)
        OR a whitelisted channel NAME (MESHANCHOR_ORACLE_MESHCORE_CHANNELS).
        MeshCore channel messages arrive as ``<channel> <sender>: <text>``, so the
        channel is matched by NAME and the embedded sender is the reply target
        (see ``_on_channel_message`` + ``_parse_meshcore_channel_text``). A DM is
        answered DIRECTED to the asker's wire pubkey_prefix; a CHANNEL query is
        answered to the group on the private reply slot — see ``_send`` for which
        leg is which and how it is decided. The audit log lives under the
        MeshAnchor data dir. Read-only — never controls services or mutates.
        """
        import os
        if str(os.environ.get("MESHANCHOR_ORACLE_ENABLED", "")).strip().lower() \
                not in ("1", "true", "yes", "on"):
            return None
        import socket

        from oracle import fetch_api_status, oracle_log_path, read_snapshot
        from oracle.responder import MeshOracleResponder
        from utils.jsonl_log import append_jsonl

        def _snapshot():
            return read_snapshot(status=fetch_api_status(), box=socket.gethostname())

        # Two reply legs, one slot, never Public. CHANNEL leg (channel is the
        # slot's NAME): the asker is a display name from the text header, not
        # a resolvable contact, so the group is answered ON the private slot
        # (bridge_target_channel; override MESHANCHOR_ORACLE_MESHCORE_REPLY_SLOT).
        # DM leg (channel is None): the asker is the wire's pubkey_prefix — a
        # real contact — so the reply is DIRECTED to them; _send_message drops
        # on contact-not-found and never falls back to a broadcast (2026-05-19).
        # Until 2026-09-20 BOTH legs broadcast on the private slot, so a DM
        # was answered onto our channel and the asker never saw it.
        try:
            _reply_slot = int(os.environ.get(
                "MESHANCHOR_ORACLE_MESHCORE_REPLY_SLOT",
                getattr(self.config.meshcore, "bridge_target_channel", 1)) or 1)
        except (TypeError, ValueError):
            _reply_slot = 1

        def _send(text: str, dest: str, channel) -> bool:
            # ``channel is None`` is the DM leg and ONLY the DM leg: the channel
            # leg passes the device's name for the slot, or UNNAMED_SLOT when the
            # device cannot name it — never None (meshcore_ingress.UNNAMED_SLOT).
            if channel is not None:
                return self.send_text(text, destination=None, channel=_reply_slot)
            # Second, independent guard, so this closure is safe whatever a
            # future call site passes: a DM target is a WIRE fact — the
            # pubkey_prefix reader.py hands us, always hex. Anything else
            # arrived from TEXT (a sender-chosen display name), and answering
            # it would DM whichever contact the asker named. Refuse, and never
            # fall back to a broadcast (the 2026-05-19 Public leak).
            if dest and _is_wire_prefix(dest):
                return self.send_text(text, destination=dest, channel=_reply_slot)
            logger.warning(
                f"oracle DM reply refused — destination {dest!r} is not a wire "
                f"pubkey_prefix; not delivered, not broadcast")
            return False

        def _log(record: dict) -> None:
            try:
                p = oracle_log_path()
                p.parent.mkdir(parents=True, exist_ok=True)
                err = append_jsonl(str(p), [record], 2 * 1024 * 1024)
                if err:  # honest_failure_modes #9: a swallow leaves a witness
                    logger.warning(f"mesh oracle audit log write failed: {err}")
            except Exception as e:  # pragma: no cover - best-effort audit log
                logger.debug(f"mesh oracle (meshcore) log append failed: {e}")

        # MeshCore channel messages arrive as "<channel> <sender>: <text>", so
        # the channel identity is a NAME (e.g. "meshanchor"), matched against
        # this lowercased name set — cleaner + more robust than the opaque,
        # per-message numeric index.
        allowed_channels = {
            tok.strip().lower()
            for tok in os.environ.get(
                "MESHANCHOR_ORACLE_MESHCORE_CHANNELS", "").split(",")
            if tok.strip()
        }

        return MeshOracleResponder.from_env(
            snapshot_fn=_snapshot, send_fn=_send, log_fn=_log,
            transport="meshcore",
            allowlist_env="MESHANCHOR_ORACLE_MESHCORE_ALLOWLIST",
            allowed_channels=allowed_channels)

