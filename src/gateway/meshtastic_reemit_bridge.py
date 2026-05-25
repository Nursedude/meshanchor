"""LXMF → MeshCore re-emit bridge.

Inverse of MeshForge's ``MeshtasticBroadcastBridge`` (which fans Meshtastic
channel broadcasts out to subscribed LXMF peers). When meshanchor-server
receives an LXMF DM from a known MeshtasticBroadcastBridge identity, this
bridge strips the wire prefix and re-emits the body on a configured
MeshCore channel. Closes the Meshtastic→MeshCore asymmetry documented in
[[meshtastic-to-meshcore-bridge-gap]].

Wire shape (from MeshForge MeshtasticBroadcastBridge.prefix_format default):

    "[meshtastic ch2:!32962f10] hello world"

After strip + reformat (default output_format "[Mesh:{sender}] {text}"):

    "[Mesh:!32962f10] hello world"

The reformatted body is sync-queued to the active
``gateway.meshcore_handler.MeshCoreHandler`` via ``send_text(..., channel=N)``;
that handler owns the serial port and dispatches to the radio on its
async loop.

Loop guards (from [[meshtastic-broadcast-bridge-2026-05-18]] lesson 3):

1. Source-identity allowlist — only DMs from configured hashes trigger
   re-emit. Stops any other LXMF traffic from getting amplified onto
   MeshCore.
2. Content-prefix drop list — Issue #66 synthetic ACK bodies
   (``[delivered:`` / ``[timeout:`` / ``[failed:``) round-trip through LoRa
   and lose their in-process flag, so a text-level guard at bridge entry
   is the right layer to break the loop.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from typing import Any, Dict, Optional, Union

from .config import GatewayConfig, MeshtasticReemitConfig

logger = logging.getLogger(__name__)


def _coerce_to_str(value: Any) -> str:
    """Render LXMF content (bytes or str) as str.

    LXMF.LXMessage.content arrives as bytes on the wire and str when the
    gateway has already decoded it. Both shapes appear in this hot path
    (see Issue #40 / `_process_rns_to_mesh`), so we handle both.
    """
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8", errors="replace")
        except Exception:
            return bytes(value).decode("latin-1", errors="replace")
    return str(value)


class MeshtasticReemitBridge:
    """LXMF DM → MeshCore channel broadcast re-emit.

    Lifecycle:

        bridge = MeshtasticReemitBridge(config.meshtastic_reemit)
        bridge.start()
        # ... rns_bridge._on_lxmf_receive calls into bridge.on_lxmf_message ...
        bridge.stop()
    """

    def __init__(
        self,
        config: MeshtasticReemitConfig,
        handler_getter: Optional[Any] = None,
    ) -> None:
        self._config = config
        # Allow tests to inject a fake "give me the active handler" function.
        # In production this defaults to meshcore_handler.get_active_handler,
        # but importing lazily avoids a circular import at module load.
        self._handler_getter = handler_getter

        # Lowercase the allowlist once at construct time so per-message
        # comparison is cheap. The config-loader also lowercases, but
        # programmatic callers might not.
        self._allowed_identities = {
            h.lower() for h in (config.source_identities or [])
        }

        try:
            self._strip_re = re.compile(config.strip_pattern)
        except re.error as e:
            logger.warning(
                "meshtastic_reemit: invalid strip_pattern %r (%s); "
                "falling back to default",
                config.strip_pattern,
                e,
            )
            self._strip_re = re.compile(
                r"^\[meshtastic ch(?P<channel>\d+):(?P<sender>[^\]]+)\]\s*"
            )

        self._running = False
        self._started_at: Optional[datetime] = None

        self._stats_lock = threading.Lock()
        self.stats: Dict[str, int] = {
            "received": 0,             # LXMF messages observed by the bridge
            "filtered_identity": 0,    # source_hash not in allowlist
            "filtered_synth_ack": 0,   # raw content matched a drop_prefix
            "filtered_nested_bridge": 0,  # post-strip body matched a
                                          # nested_drop_prefix (e.g. MeshCore
                                          # content that round-tripped via
                                          # _process_rns_to_mesh on a peer
                                          # gateway → would re-emit a
                                          # message that ORIGINATED on
                                          # MeshCore back onto MeshCore)
            "filtered_empty": 0,       # post-decode content empty
            "reemitted": 0,            # send_text queued successfully
            "errors": 0,
            "handler_unavailable": 0,  # get_active_handler returned None
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> bool:
        if self._running:
            return True
        if not self._config.enabled:
            logger.debug("meshtastic_reemit: not enabled — skipping start")
            return False
        if not self._allowed_identities:
            logger.warning(
                "meshtastic_reemit: enabled but source_identities is empty — "
                "bridge will receive nothing"
            )
        self._running = True
        self._started_at = datetime.utcnow()
        logger.info(
            "meshtastic_reemit: started — %d source identity(ies), target "
            "channel %d, output %r",
            len(self._allowed_identities),
            self._config.target_channel,
            self._config.output_format,
        )
        return True

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._started_at = None
        logger.info("meshtastic_reemit: stopped")

    # ------------------------------------------------------------------
    # Inbound hook
    # ------------------------------------------------------------------

    def on_lxmf_message(
        self,
        source_hash: Union[bytes, str],
        content: Any,
    ) -> bool:
        """Process one LXMF inbound message.

        Returns True if the message was re-emitted on MeshCore, False
        otherwise (filtered, dropped, or handler unavailable).

        Called from ``RNSMeshtasticBridge._on_lxmf_receive`` after the
        existing flow. Never raises — any exception is caught and logged
        because this is on the LXMF hot path.
        """
        if not self._running:
            return False
        try:
            with self._stats_lock:
                self.stats["received"] += 1

            # 1) Source-identity allowlist. Anything from a non-trusted
            # LXMF sender (including stray DMs to the gateway identity)
            # gets rejected before any prefix work.
            source_hex = (
                source_hash.hex()
                if isinstance(source_hash, (bytes, bytearray))
                else str(source_hash)
            ).lower()
            if source_hex not in self._allowed_identities:
                with self._stats_lock:
                    self.stats["filtered_identity"] += 1
                return False

            text = _coerce_to_str(content)
            if not text:
                with self._stats_lock:
                    self.stats["filtered_empty"] += 1
                return False

            # 2) Synth-ACK loop guard. Issue #66 ACK bodies round-trip
            # through LoRa and lose any in-process flag — content-prefix
            # is the only signal at this layer.
            for prefix in self._config.drop_prefixes or ():
                if text.startswith(prefix):
                    with self._stats_lock:
                        self.stats["filtered_synth_ack"] += 1
                    logger.debug(
                        "meshtastic_reemit: dropping synth-ACK shaped "
                        "content from %s: %r",
                        source_hex[:8], text[:32],
                    )
                    return False

            # 3) Strip the wire prefix and capture sender/channel if the
            # regex provides them as named groups. Bodies that don't
            # match still fall through with the whole body as `text` and
            # placeholder sender/channel — defensive against future
            # MeshtasticBroadcastBridge prefix-format changes.
            match = self._strip_re.match(text)
            if match:
                groups = match.groupdict()
                sender = groups.get("sender") or "?"
                channel_str = groups.get("channel") or "?"
                stripped = text[match.end():]
            else:
                sender = "?"
                channel_str = "?"
                stripped = text

            # 3b) Nested-bridge loop guard (post-strip). The wire content
            # we just stripped was added by the MeshtasticBroadcastBridge
            # on a peer gateway when it picked up a Meshtastic RX. But
            # that Meshtastic RX might itself have been a peer gateway's
            # `_process_rns_to_mesh` re-broadcast of an LXMFBroadcastBridge
            # fan-out — in which case the stripped body still carries the
            # original protocol's bridge tag (e.g. `[MeshCore] ...`).
            # Without this guard, we'd take a MeshCore broadcast that
            # took a long detour through LXMF and Meshtastic and re-emit
            # it back onto MeshCore — closing the loop. Field-detected
            # 2026-05-18 23:02 HST: p3 broadcasts on MeshCore "meshanchor"
            # appeared back on MeshCore Public after one full round
            # through Meshtastic ch2.
            for prefix in self._config.nested_drop_prefixes or ():
                if stripped.startswith(prefix):
                    with self._stats_lock:
                        self.stats["filtered_nested_bridge"] += 1
                    logger.debug(
                        "meshtastic_reemit: dropping nested-bridge content "
                        "from %s (prefix %r): %r",
                        source_hex[:8], prefix, stripped[:48],
                    )
                    return False

            # 4) Reformat using operator's output_format. Bad templates
            # fall back to a safe default so a typo in config doesn't
            # take the bridge down.
            try:
                body = self._config.output_format.format(
                    sender=sender,
                    text=stripped,
                    channel=channel_str,
                )
            except (KeyError, IndexError, ValueError) as e:
                logger.warning(
                    "meshtastic_reemit: bad output_format %r (%s) — "
                    "using fallback",
                    self._config.output_format, e,
                )
                body = f"[Mesh:{sender}] {stripped}"

            # 5) Dispatch to the active MeshCoreHandler. Pulled lazily
            # because handlers come and go on disconnect/reconnect.
            handler = self._resolve_handler()
            if handler is None:
                with self._stats_lock:
                    self.stats["handler_unavailable"] += 1
                logger.debug(
                    "meshtastic_reemit: no active MeshCore handler; "
                    "dropping re-emit for %s",
                    source_hex[:8],
                )
                return False

            ok = handler.send_text(
                body, destination=None, channel=self._config.target_channel,
            )
            if ok:
                with self._stats_lock:
                    self.stats["reemitted"] += 1
                logger.info(
                    "meshtastic_reemit: re-emitted on MeshCore ch%d from "
                    "%s (orig ch%s sender %s)",
                    self._config.target_channel,
                    source_hex[:8],
                    channel_str,
                    sender,
                )
                return True
            # send_text returned False (MeshCore send refused/failed) — count
            # AND log it. Previously this bumped stats["errors"] silently, so a
            # reemit send-failure left no journal trace (only the exception
            # path below logged). Mirrors the success INFO line's fields.
            with self._stats_lock:
                self.stats["errors"] += 1
            logger.warning(
                "meshtastic_reemit: MeshCore send_text returned False — "
                "re-emit dropped on ch%d from %s (orig ch%s sender %s)",
                self._config.target_channel,
                source_hex[:8],
                channel_str,
                sender,
            )
            return False

        except Exception as e:
            logger.error("meshtastic_reemit: unexpected error: %s", e)
            with self._stats_lock:
                self.stats["errors"] += 1
            return False

    # ------------------------------------------------------------------
    # Handler resolution
    # ------------------------------------------------------------------

    def _resolve_handler(self):
        """Resolve the active MeshCoreHandler.

        Lazy import to avoid the rns_bridge → meshcore_handler import
        cycle and to keep this module testable in isolation. Tests can
        inject ``handler_getter`` at construct time.
        """
        if self._handler_getter is not None:
            return self._handler_getter()
        try:
            from .meshcore_handler import get_active_handler
        except Exception as e:
            logger.debug("meshtastic_reemit: meshcore_handler import: %s", e)
            return None
        return get_active_handler()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        with self._stats_lock:
            stats_copy = dict(self.stats)
        return {
            "running": self._running,
            "enabled": self._config.enabled,
            "source_identities": sorted(self._allowed_identities),
            "target_channel": int(self._config.target_channel),
            "output_format": self._config.output_format,
            "drop_prefixes": list(self._config.drop_prefixes or ()),
            "nested_drop_prefixes": list(self._config.nested_drop_prefixes or ()),
            "started_at_iso": (
                self._started_at.isoformat() if self._started_at else None
            ),
            "stats": stats_copy,
        }


def create_from_gateway_config(
    gateway_config: GatewayConfig,
) -> Optional[MeshtasticReemitBridge]:
    """Convenience factory — returns None if the plug-in is disabled.

    Designed to be called from RNSMeshtasticBridge after LXMF is up.
    """
    cfg = getattr(gateway_config, "meshtastic_reemit", None)
    if cfg is None or not cfg.enabled:
        return None
    return MeshtasticReemitBridge(cfg)
