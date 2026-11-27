"""Leg helpers for gateway.mesh_bridge (MF025 headroom; port of MeshForge's
``_mesh_bridge_legs.py``, 2026-11-11). Logs on the ``gateway.mesh_bridge``
logger so log-capturing tests that knew these lines before the split still
find them.

MeshAnchor keeps ``_build_downlink_injector`` as a bridge method; the rest of
MeshForge's module is here: the write-target / gave-up reads (BR-T #10), the
per-message event line + queue drop witness (C21), the outage-hold constants
(BR-T #14) and the ONE DM-destination rule (BR-T #18).
"""

from __future__ import annotations

import logging
import os
import threading

from utils.safe_import import safe_import

from .message_queue_models import _env_seconds
from .message_queue_outage_mixin import TERMINAL_REASON_KEY

logger = logging.getLogger("gateway.mesh_bridge")


# The library CLASSES, imported apart from mesh_bridge's module handles (tests
# replace those modules with fakes): the write-target check below asks what the
# library's own _writeBytes gates on, so it must recognise the real types.
_TCPInterfaceCls, _HAS_TCP_CLS = safe_import('meshtastic.tcp_interface', 'TCPInterface')
_StreamInterfaceCls, _HAS_STREAM_CLS = safe_import(
    'meshtastic.stream_interface', 'StreamInterface')


def _write_target_gone(interface) -> bool:
    """True when the meshtastic library would DROP a write to ``interface``
    without a word (BR-T #10, 2026-11-10).

    ``TCPInterface._writeBytes`` writes only ``if self.socket is not None`` (a
    failed ``_reconnect()`` or a ``close()`` leaves it None) and the serial
    ``StreamInterface._writeBytes`` only ``if self.stream:``; either way
    ``sendText`` still returns a packet. This reads exactly what those gates
    read. Any other interface (MQTT leg, a fake) is not judged here: False.
    """
    if _HAS_TCP_CLS and isinstance(interface, _TCPInterfaceCls):
        return interface.socket is None
    if _HAS_STREAM_CLS and isinstance(interface, _StreamInterfaceCls):
        return not interface.stream
    return False


def _library_gave_up(interface) -> bool:
    """True when a meshtastic library stream interface reads not-connected:
    its ``_disconnected()`` clears ``isConnected`` — when the reader thread
    exits (the link is gone for good), and also on a ``FromRadio.rebooted``,
    after which the library re-configs the SAME object and sets it again (r3
    finding 2: an earlier docstring claimed "nothing sets it again"). Both also
    publish ``connection.lost``, so an installed leg is torn down and
    reconnected either way; this read only decides a leg that MISSED that
    event. During the library's own 1 s ``_reconnect()`` it stays SET, so a
    self-healing link is never judged dead. Non-library interfaces: False.

    The backstop for a missed ``connection.lost`` (11-10 review r2 F-A: one
    that fires before the leg's handle is installed is dropped by the identity
    check, and the leg read connected on a dead handle forever)."""
    if not (_HAS_STREAM_CLS and isinstance(interface, _StreamInterfaceCls)):
        return False
    flag = getattr(interface, "isConnected", None)
    return isinstance(flag, threading.Event) and not flag.is_set()


def bridge_event(verb: str, side: str, text: str, reason: str = "") -> None:
    """One DEBUG line per text the bridge heard, dropped or forwarded.

    ``mesh_bridge rx leg=<side>: <text>`` / ``mesh_bridge drop leg=<side>
    reason=<reason>: <text>`` / ``mesh_bridge tx dest=<side>: <text>``. The
    per-message record a run reconciles against (MeshForge C21, 2026-10-19):
    before it a drop only moved a counter. Same format as MeshForge's twin
    (its scripts/c21_bridge_run.py parses it) — change both together.
    """
    where = f"dest={side}" if verb == "tx" else f"leg={side}"
    why = f" reason={reason}" if reason else ""
    logger.debug("mesh_bridge %s %s%s: %s", verb, where, why, text[:120])


def queue_drop_witness(ingress: str):
    """on_terminal hook for a bridge queue: name a row the queue gave up on —
    by WHY (MF §B #20): ``expired`` (the #14 hold aged out), ``shed`` (queue
    pressure), ``refused`` (MA's SendRefused), else ``dead_letter`` (retries
    spent; MF's c21 keys on that name)."""
    def on_terminal(payload, outcome: str) -> None:
        if outcome == "dropped":
            payload = payload or {}
            bridge_event("drop", ingress, str(payload.get("content", "")),
                         payload.get(TERMINAL_REASON_KEY) or "dead_letter")
    return on_terminal


def _max_age_from_env() -> int:
    """One knob for both twins on a box (the operator Q18 pattern): the shared
    MESHFORGE_MESH_BRIDGE_MAX_AGE_S wins; MESHANCHOR_… is the fallback."""
    if os.environ.get("MESHFORGE_MESH_BRIDGE_MAX_AGE_S"):
        return _env_seconds("MESHFORGE_MESH_BRIDGE_MAX_AGE_S", 600)
    return _env_seconds("MESHANCHOR_MESH_BRIDGE_MAX_AGE_S", 600)


#: BR-T #14 (MF 2026-11-11): a row held while its leg is down ages out,
#: witnessed (queue_drop_witness -> `drop … dead_letter`), after this long.
#: ASSERTED, not measured (operator 11-11: "10 min, measure later" — best
#: effort, not 100%): covers a meshtasticd restart; chat relayed later than
#: this reads as new on the far mesh. Applies to EVERY pending row, held or
#: not — incl. rows persisted across a bridge restart (MF r2 F-2: accepted,
#: named). Re-derive from the leg-outage history (MF deferred:
#: mesh-bridge-leg-outage-measure-2026-11-11).
MESH_BRIDGE_MAX_AGE_S = _max_age_from_env()
#: How soon a row deferred on a down leg is asked again (no attempt spent).
LINK_DOWN_RETRY_S = 2.0
#: Deferrals closer together than this are ONE outage (logged ERROR once);
#: a gap longer starts a new one (MF r2 F-3: rows that aged out, an idle leg).
LINK_DOWN_EPISODE_GAP_S = 30.0


# The wire-id rules (BR-T #18 + MF's 11-17 sender fix, bbae6ee8) live in the
# dependency-free wire_ids module (byte-identical with MeshForge's); re-exported
# so the existing imports keep working.
from .wire_ids import dm_destination, node_dest, packet_origin  # noqa: E402,F401


def bump_stat(bridge, key: str) -> None:
    """One locked counter bump (the bridge's stats are shared across threads)."""
    with bridge._stats_lock:
        bridge.stats[key] = bridge.stats.get(key, 0) + 1
