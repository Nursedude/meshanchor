"""
MeshCore Bridge Mixin - MeshCore-specific message bridging logic.

Extracted from rns_bridge.py to reduce file size per CLAUDE.md guidelines.
Provides _process_meshcore_to_bridge and _process_bridge_to_meshcore methods
that route messages between MeshCore and other networks (Meshtastic, RNS).
"""

import logging
from typing import Optional

from .bridge_health import SubsystemState, MessageOrigin

logger = logging.getLogger(__name__)

# MeshCore-origin bridge markers. Content carrying any of these (possibly
# nested after a wire prefix) originated on MeshCore and must never be
# re-injected onto MeshCore (split-horizon — see _process_*_to_meshcore).
# "[MC:" = MeshCore→Meshtastic egress; "[ch0:"/"[ch1:" = LXMFBroadcast
# fan-out tags; "[MeshCore]" = legacy marker. Kept in sync with the
# echo-loop invariant documented in gateway/config.py.
_MESHCORE_ORIGIN_MARKERS = ("[MC:", "[ch0:", "[ch1:", "[MeshCore]")


def parse_meshcore_channel_header(content: str):
    """Split a MeshCore channel broadcast's baked-in header from its body.

    MeshCore firmware prepends a ``"<channel> <sender>: "`` header to channel
    broadcast text. The gateway sees that header *inside* ``content`` because
    ``source_address`` is empty for channel broadcasts (so the bridge would
    otherwise label them ``[MC:unknown]`` and leave the header in the body).
    Example: ``"meshanchor p4: wx"`` → ``("p4", "wx")``.

    Splitting on the FIRST ``": "`` keeps a ``:`` inside the body intact
    (URLs, clock times, ``"hey all: listen"``). The sender is the last
    whitespace token of the header (the channel name precedes it). Returns
    ``("", content)`` unchanged when no header separator is present, so
    unprefixed text falls through to the caller's default labelling.

    Phase 2 (2026-05-24): lifting the bare command to index 0 is what lets
    the meshing-around bot (``explicitCmd=True``, only acts on index 0)
    actually trigger on commands bridged in from the MeshCore channel.
    """
    sep = ": "
    idx = content.find(sep)
    if idx <= 0:
        return "", content
    header = content[:idx]
    body = content[idx + len(sep):]
    tokens = header.split()
    sender = tokens[-1] if tokens else ""
    if not sender or not body:
        return "", content
    return sender, body


class MeshCoreBridgeMixin:
    """Mixin providing MeshCore-specific bridge processing methods.

    Expects the following attributes on the host class:
    - self._meshcore_handler: MeshCoreHandler instance (or None)
    - self.health: BridgeHealthMonitor
    - self.stats: dict with stats counters
    - self._stats_lock: threading.Lock
    - self.config: GatewayConfig
    - self.send_to_meshtastic(): method
    - self.send_to_rns(): method
    - self._notify_message(): method
    - self._requeue_failed_message(): method
    """

    def _meshcore_loop(self):
        """Main loop for MeshCore connection - delegates to handler."""
        if self._meshcore_handler:
            self._meshcore_handler.run_loop()

    def send_to_meshcore(self, message: str, destination: str = None,
                         channel: int = -1) -> bool:
        """Send a message to MeshCore network.

        Args:
            message: Text content to send
            destination: Destination address (None for broadcast)
            channel: Channel slot. ``-1`` (sentinel) means the caller did
                not specify a slot — for broadcasts this is REJECTED
                rather than silently routed to slot 0 (Public). DMs
                ignore the channel arg (they go to the contact).

        Returns:
            True if queued successfully, False otherwise.
        """
        if not self._meshcore_handler:
            logger.warning("MeshCore handler not initialized")
            return False
        if destination is None and channel < 0:
            # Privacy-class refusal — channel-0 Public leak follow-up
            # (2026-05-20). The pre-fix kwarg-default routed every
            # unspecified-channel broadcast to slot 0; closing that door
            # at the wrapper too means a future caller that forgets
            # ``channel=`` can never resurrect the leak.
            with self._stats_lock:
                self.stats.setdefault(
                    'meshcore_bridge_default_channel_drop', 0)
                self.stats['meshcore_bridge_default_channel_drop'] += 1
            logger.warning(
                "send_to_meshcore: broadcast with no channel specified; "
                "dropping rather than defaulting to slot 0 (Public). "
                "Caller must pass channel= explicitly."
            )
            return False
        return self._meshcore_handler.send_text(message, destination, channel)

    def _process_meshcore_to_bridge(self, msg) -> None:
        """Process message from MeshCore → other networks (Meshtastic, RNS).

        MeshCore messages arrive as CanonicalMessage or BridgedMessage.
        Routes to Meshtastic and/or RNS based on routing rules.
        """
        try:
            # Extract content — handle both CanonicalMessage and BridgedMessage
            if hasattr(msg, 'source_address'):
                # CanonicalMessage
                src_label = msg.source_address[:8] if msg.source_address else 'unknown'
                content = msg.content
                is_broadcast = msg.is_broadcast
                via_internet = getattr(msg, 'via_internet', False)
            else:
                # BridgedMessage
                src_label = msg.source_id[:8] if msg.source_id else 'unknown'
                content = msg.content
                is_broadcast = msg.is_broadcast
                via_internet = getattr(msg, 'via_internet', False)

            # MeshCore channel broadcasts bake a "<channel> <sender>: <text>"
            # header into the text (source_address is empty → src_label
            # 'unknown'). Lift the sender out and drop the header so the
            # bridged form is "[MC:<sender>] <text>": the meshing-around bot on
            # the Meshtastic side strips the leading "[...]" bridge tag,
            # leaving a bare command at index 0 so wx/cmd/… actually trigger it
            # (explicitCmd=True only acts on index 0). The "[MC:" prefix is
            # preserved so the LXMF re-emit loop guard (nested_drop_prefixes)
            # still drops echoes. (Phase 2, 2026-05-24.)
            label, body = src_label, content
            if is_broadcast:
                parsed_sender, parsed_body = parse_meshcore_channel_header(content)
                if parsed_sender:
                    label, body = parsed_sender, parsed_body
            prefix = f"[MC:{label}] "
            bridged_content = prefix + body

            # Route to Meshtastic. Normally gated on a live local radio,
            # but a radio-less gateway can still egress to a peer meshtasticd
            # when meshtastic_egress is configured (send_to_meshtastic handles
            # the remote send_text_direct fallback).
            mesh_state = self.health.get_subsystem_state("meshtastic")
            egress = getattr(self.config, "meshtastic_egress", None)
            egress_on = bool(egress and getattr(egress, "enabled", False) and egress.host)
            if egress_on or mesh_state not in (SubsystemState.DISCONNECTED, SubsystemState.DISABLED):
                if self.send_to_meshtastic(bridged_content,
                                           channel=self.config.meshtastic.channel):
                    logger.info(f"Bridge MC→Mesh: {bridged_content[:50]}...")
                    with self._stats_lock:
                        self.stats.setdefault('messages_meshcore_to_mesh', 0)
                        self.stats['messages_meshcore_to_mesh'] += 1
                    self.health.record_message_sent("meshcore_to_mesh")
                else:
                    logger.warning("Failed to bridge MC→Mesh")
                    with self._stats_lock:
                        self.stats['errors'] += 1
                    self.health.record_message_failed("meshcore_to_mesh", requeued=False)

            # Route to RNS (only if not via internet — MeshCore is pure radio)
            rns_state = self.health.get_subsystem_state("rns")
            if rns_state not in (SubsystemState.DISCONNECTED, SubsystemState.DISABLED):
                # RNS broadcast requires propagation node, send if available
                if self.send_to_rns(bridged_content):
                    logger.info(f"Bridge MC→RNS: {bridged_content[:50]}...")
                    with self._stats_lock:
                        self.stats.setdefault('messages_meshcore_to_rns', 0)
                        self.stats['messages_meshcore_to_rns'] += 1
                    self.health.record_message_sent("meshcore_to_rns")
                else:
                    logger.debug("MC→RNS: not sent (no RNS propagation)")

        except Exception as e:
            logger.error(f"Error bridging MeshCore→Bridge: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1

    def _process_bridge_to_meshcore(self, msg) -> None:
        """Process message from other networks → MeshCore.

        Handles text truncation to MeshCore's ~160 byte limit.
        Filters internet-originated messages (MeshCore is pure radio).
        """
        try:
            # Check internet origin filter — MeshCore is pure radio
            if hasattr(msg, 'via_internet') and msg.via_internet:
                logger.debug("Dropping internet-origin message destined for MeshCore")
                return
            if hasattr(msg, 'origin') and msg.origin == MessageOrigin.MQTT:
                logger.debug("Dropping MQTT-origin message destined for MeshCore")
                return

            # Extract content
            if hasattr(msg, 'source_address'):
                src_net = msg.source_network
                src_label = msg.source_address[:8] if msg.source_address else 'unknown'
                content = msg.content
            else:
                src_net = msg.source_network
                src_label = msg.source_id[:8] if msg.source_id else 'unknown'
                content = msg.content

            net_prefix = "Mesh" if src_net == "meshtastic" else "RNS"

            # Split-horizon echo guard (p4 self-echo, 2026-05-26). Content
            # carrying a MeshCore-origin marker ORIGINATED on MeshCore and
            # round-tripped back via Meshtastic/RNS; re-injecting it onto
            # MeshCore bounces it to the original sender (who, being on the
            # channel, sees their own message returned tagged [RNS:..] —
            # 2-3x, once per relaying gateway). Other nodes are unaffected,
            # so this is purely the originator's echo. Markers may be nested
            # after a wire prefix (e.g. "[meshtastic ch2:!x] [MC:p4] hi"),
            # so we substring-search rather than startswith. Genuine
            # Meshtastic/RNS-origin forward delivery carries no MeshCore
            # marker and is unaffected. Sibling of the meshtastic_reemit
            # nested_drop guard; mirrors MeshForge is_already_bridged.
            if any(m in (content or "") for m in _MESHCORE_ORIGIN_MARKERS):
                with self._stats_lock:
                    self.stats.setdefault('meshcore_bridge_echo_loop_drop', 0)
                    self.stats['meshcore_bridge_echo_loop_drop'] += 1
                logger.debug(
                    "Bridge %s→MC: dropping MeshCore-origin content "
                    "(split-horizon echo guard): %r",
                    net_prefix, (content or "")[:60],
                )
                return

            prefix = f"[{net_prefix}:{src_label}] "
            bridged_content = prefix + content

            # MeshCore has ~160 byte text limit — truncate if needed
            if len(bridged_content.encode('utf-8')) > 160:
                from .canonical_message import _truncate_utf8
                bridged_content = _truncate_utf8(bridged_content, 160)

            # Channel resolution — channel-0 Public leak follow-up
            # (2026-05-20). Previously this call site omitted ``channel=``
            # which defaulted to 0 (Public), leaking every cross-protocol
            # broadcast onto the public slot. Resolve order:
            #   1. Per-message metadata (carries origin's channel intent)
            #   2. config.meshcore.bridge_target_channel (operator-set)
            #   3. Drop with counter — never silently broadcast on slot 0
            target_channel = self._resolve_bridge_target_channel(msg)
            if target_channel < 0:
                with self._stats_lock:
                    self.stats.setdefault(
                        'meshcore_bridge_default_channel_drop', 0)
                    self.stats['meshcore_bridge_default_channel_drop'] += 1
                logger.warning(
                    f"Bridge {net_prefix}→MC: no channel resolved "
                    f"(metadata empty, config.meshcore.bridge_target_channel "
                    f"unset); dropping rather than leaking to slot 0 (Public). "
                    f"Set config.meshcore.bridge_target_channel to enable."
                )
                self.health.record_message_failed(
                    f"mesh_to_meshcore" if src_net == "meshtastic" else "rns_to_meshcore",
                    requeued=False,
                )
                return

            if self.send_to_meshcore(bridged_content, channel=target_channel):
                direction = f"{src_net[:4]}_to_meshcore"
                logger.info(f"Bridge {net_prefix}→MC ch{target_channel}: {bridged_content[:50]}...")
                with self._stats_lock:
                    key = f'messages_{src_net}_to_meshcore'
                    self.stats.setdefault(key, 0)
                    self.stats[key] += 1
                self.health.record_message_sent(f"mesh_to_meshcore"
                                                if src_net == "meshtastic"
                                                else "rns_to_meshcore")
            else:
                logger.warning(f"Failed to bridge {net_prefix}→MC")
                with self._stats_lock:
                    self.stats['errors'] += 1
                requeued = self._requeue_failed_message(msg, "meshcore")
                self.health.record_message_failed(
                    f"mesh_to_meshcore" if src_net == "meshtastic" else "rns_to_meshcore",
                    requeued=requeued,
                )

        except Exception as e:
            logger.error(f"Error bridging →MeshCore: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1

    def _resolve_bridge_target_channel(self, msg) -> int:
        """Resolve the MeshCore slot for a cross-protocol bridge message.

        Config-only. ``msg.metadata['channel']`` is IGNORED for cross-
        protocol bridge cargo because it carries the SOURCE protocol's
        channel index (Meshtastic channel index, or RNS context), which
        has no meaningful mapping to a MeshCore slot. Honoring it
        preserved the leak: every Meshtastic broadcast on Meshtastic
        channel 0 ended up on MeshCore slot 0 (Public) — exactly the
        privacy bug Issue #37 was meant to close. Matches the symmetric
        MC→Meshtastic direction in `_process_meshcore_to_bridge`, which
        also uses `config.meshtastic.channel` and never preserves the
        MeshCore source slot.

        Returns:
          - ``config.meshcore.bridge_target_channel`` if >= 0
          - -1 (caller drops the message) otherwise
        """
        meshcore_cfg = getattr(self.config, 'meshcore', None)
        cfg_slot = getattr(meshcore_cfg, 'bridge_target_channel', -1)
        try:
            cfg_slot = int(cfg_slot)
        except (TypeError, ValueError):
            cfg_slot = -1
        return cfg_slot if cfg_slot >= 0 else -1
