"""Directed-DM reply leg — pure helpers (prototype 2026-08-28).

The use case (operator, 2026-08-28): MeshCore public-channel messages already
reach the Meshtastic side via the MC→RNS→Mesh composition, but a Meshtastic
user has no way to answer — the RNS→MC return leg lands on the private bridge
slot, which public-channel folks never see, and blanket-bridging a Meshtastic
channel onto MeshCore Public would spam a community space. This leg gives the
Meshtastic side a *deliberate, addressed* way to speak: a reply shaped

    @<contact> <text>

is delivered as a MeshCore **DM** to the matching contact (name or
pubkey-prefix, the same matching ``_find_contact`` already does) instead of a
channel broadcast — and because MeshCore DMs carry a protocol-level path ACK,
the ack is fed back to the originating mesh as a ``[MC:reply]`` notice, closing
the syn/ack loop end to end. Public channel broadcasts have no protocol ACK
at all, so a DM is the only shape on which "did they get it?" is answerable.

This module holds the protocol-free pieces: the reply parser and the
pending-ack registry. The bridge/handler wiring lives in
``meshcore_bridge_mixin.py`` (route decision) and ``meshcore_handler.py``
(send + ack correlation). Kill-switch: ``config.meshcore.dm_replies_enabled``.
"""

import re
import time
from typing import Any, Dict, NamedTuple, Optional

# Bounded tag peeling: content arriving over the bridge carries 1-3 leading
# wire tags (e.g. "[RNS:627f] [meshtastic ch2:!abc] @p4 hi"); 4 is headroom,
# and the bound keeps a pathological input from looping.
_MAX_LEADING_TAGS = 4

_TAG_RE = re.compile(r'^\[([^\[\]]{1,60})\]\s+')

# A Meshtastic-side wire tag names the actual human sender — worth carrying
# into the DM so the MeshCore recipient knows who is talking. Both the
# "[meshtastic ch2:!abc123]" wire form and the "[Mesh:sender]" bridge form.
_MESH_SENDER_RE = re.compile(r'^(?:meshtastic\s+ch\d+|Mesh):(.+)$', re.IGNORECASE)

# "@<contact> <text>": contact is one whitespace-free token (MeshCore
# adv_names with spaces can be addressed by pubkey prefix instead); the text
# must be non-empty — a bare "@name" is not a reply.
_AT_RE = re.compile(r'^@(\S{1,32})\s+(\S[\s\S]*)$')


class DirectedReply(NamedTuple):
    contact_query: str   # what to match against contacts (name or pubkey prefix)
    reply_text: str      # the reply body, tags and @-address stripped
    origin_label: str    # best available sender identity ('' when unknown)


def parse_directed_reply(content: str) -> Optional[DirectedReply]:
    """Parse a bridged message into a directed MeshCore reply, if it is one.

    Returns None for anything that is not "@<contact> <text>" after peeling
    the leading wire tags — the caller then routes it down the existing
    channel-broadcast path unchanged. Never raises on odd input; a parse
    failure is an ordinary None (this sits on the bridge hot path).
    """
    if not content:
        return None
    body = content
    origin = ""
    for _ in range(_MAX_LEADING_TAGS):
        m = _TAG_RE.match(body)
        if not m:
            break
        tag = m.group(1)
        sender = _MESH_SENDER_RE.match(tag)
        if sender:
            origin = sender.group(1).strip()
        body = body[m.end():]
    at = _AT_RE.match(body)
    if not at:
        return None
    return DirectedReply(
        contact_query=at.group(1),
        reply_text=at.group(2).strip(),
        origin_label=origin,
    )


class PendingDmAcks:
    """Bounded registry of DM sends awaiting their MeshCore path ACK.

    ``register`` stores the ``expected_ack`` hex from a send_msg result with
    a monotonic deadline; ``pop`` claims it when the matching ACK event
    arrives. Expired entries are pruned on every call — an ACK that never
    comes simply ages out (no negative notice is emitted from absence:
    a missing ACK is unobservable delivery, not proven failure, and radio
    paths legitimately take retries). Only touched from the handler's single
    asyncio loop, so no lock.
    """

    def __init__(self, max_pending: int = 32):
        self._max = max_pending
        self._pending: Dict[str, Dict[str, Any]] = {}

    def register(self, code_hex: str, ctx: Dict[str, Any],
                 timeout_s: float) -> None:
        self.prune()
        if len(self._pending) >= self._max:
            # Evict the oldest-deadline entry rather than refuse: the newest
            # send is the one the operator is watching right now.
            oldest = min(self._pending, key=lambda k: self._pending[k]["deadline"])
            del self._pending[oldest]
        self._pending[code_hex.lower()] = {
            "ctx": dict(ctx),
            "deadline": time.monotonic() + max(2.0, timeout_s),
        }

    def pop(self, code_hex: str) -> Optional[Dict[str, Any]]:
        self.prune()
        entry = self._pending.pop((code_hex or "").lower(), None)
        return entry["ctx"] if entry else None

    def prune(self) -> None:
        now = time.monotonic()
        for k in [k for k, v in self._pending.items() if v["deadline"] < now]:
            del self._pending[k]

    def __len__(self) -> int:
        return len(self._pending)


class RecentTextDedup:
    """Short-window dedup for directed DMs.

    The same "@<contact> <text>" reply legitimately arrives at this bridge
    more than once — every MeshForge gateway that heard it on RF fans it out
    to the same LXMF destinations — and a human must not receive the same DM
    twice. Keyed on (contact, text) with a monotonic window; bounded; pruned
    on every call. Single-thread use (the bridge loop)."""

    def __init__(self, window_s: float = 60.0, max_entries: int = 64):
        self._window = window_s
        self._max = max_entries
        self._seen: Dict[tuple, float] = {}

    def seen(self, contact: str, text: str) -> bool:
        """True if this (contact, text) was already sent inside the window.
        Records the sighting either way."""
        now = time.monotonic()
        for k in [k for k, t in self._seen.items() if now - t > self._window]:
            del self._seen[k]
        key = (contact, text)
        hit = key in self._seen
        if not hit and len(self._seen) >= self._max:
            oldest = min(self._seen, key=self._seen.get)
            del self._seen[oldest]
        self._seen[key] = now
        return hit


def ack_code_hex(value: Any) -> str:
    """Normalize an expected_ack / ACK-event code to lowercase hex.

    meshcore_py hands ``expected_ack`` as bytes on the send result and
    ``code`` as a hex string on the ACK event; both funnel through here so
    the registry keys always compare equal. Unrecognized shapes normalize
    to '' (never registered, never matched — the notice simply isn't sent,
    which is the honest outcome for an uncorrelatable ACK).
    """
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, str):
        return value.strip().lower()
    return ""
