"""MeshCore INGRESS — what the gateway reads off the wire, and what it says.

Two pure pieces that used to live in ``meshcore_handler.py`` (split out
2026-09-18 when the handler hit the 1,500-line cap; the seam is real: this
is the boundary where a library event becomes a message, and the 09-18
incident lived exactly here).

* :func:`parse_meshcore_channel_text` — the header lift for the oracle's
  name gate. It is now a thin wrapper over the ONE split in
  ``meshcore_bridge_mixin`` (there were two divergent parsers of this
  header; honest_failure_modes #5). ⚠️ The header is the sending node's
  NAME (``"meshanchor p4: wx"`` from node "meshanchor p4"), not
  ``"<channel> <sender>"``. Channel identity is ``channel_idx`` on the
  event → ``metadata['channel']``; the oracle gate is owed a move to it.
* :func:`disclose_channel_ingress` — the witness that would have caught
  the root cause at write time: one INFO line per channel message naming
  the slot INDEX the wire delivered (``None`` = the payload named no slot,
  which is unknown, never Public), plus the payload's real keys for the
  first messages of a process so the wire shape lands in the journal
  instead of being assumed from a fixture.
"""

import logging
import os
import threading
from typing import Any, Dict, Optional, Tuple

from .meshcore_bridge_mixin import _split_meshcore_channel_header

logger = logging.getLogger(__name__)

#: MeshCore's Public channel is slot 0 on every companion radio.
MESHCORE_PUBLIC_CHANNEL = 0

#: Env override for the inbound source-slot allowlist (comma-separated slot
#: indices). Precedence: env > declared config (``meshcore.bridge_source_channels``)
#: > default (everything but Public). Same shape as MeshForge's
#: ``MESHFORGE_MESHCORE_BRIDGE_CHANNELS`` so the twins read alike.
BRIDGE_CHANNELS_ENV = "MESHANCHOR_MESHCORE_BRIDGE_CHANNELS"


def parse_meshcore_channel_text(content: Optional[str]) -> Tuple[Optional[str], str, str]:
    """Split channel text into ``(name_prefix_lower_or_None, label, text)``.

    Contract kept from the handler's original: the first element is
    lowercased and ``None`` when the header names nothing before the label
    (so the name-scoped oracle declines — fail-closed); ``text`` is stripped.
    Pure helper. Everything before the last header token is the "name
    prefix" — it is NOT a channel, see the module docstring.
    """
    name_prefix, label, body = _split_meshcore_channel_header(content or "")
    return (name_prefix.lower() or None), label, body.strip()


def reach_of(payload: Any) -> str:
    """``hops=<n|direct|?> snr=<dB|?>`` from the wire's own fields.

    Wired 2026-09-18 for the Public-bot question ("how effective for users
    out of LOS?"): meshcore_py delivers ``path_len`` (repeater hops; 0 or the
    255 marker = heard direct) and ``SNR`` (dB) on every CHANNEL_MSG_RECV, and
    nothing read them — a free reach census of every sender, including the
    Public traffic the policy refuses. '?' = the field was absent (older
    frame without the logged-packet lookup), never a guess.
    """
    if not isinstance(payload, dict):
        return "hops=? snr=?"
    pl = payload.get('path_len')
    if pl is None:
        hops = "?"
    elif pl == 255 or pl == 0:
        hops = "direct"
    else:
        hops = str(pl)
    snr = payload.get('SNR')
    snr_s = "?" if snr is None else f"{snr:g}"
    return f"hops={hops} snr={snr_s}"


def disclose_channel_ingress(handler: Any, event: Any, msg: Any) -> None:
    """Log the slot index the wire delivered (and, at first, the payload keys).

    ``handler`` carries the per-process counter; ``msg`` is the
    CanonicalMessage ``from_meshcore`` built (``metadata['channel']`` is the
    ``channel_idx`` or ``None``). Never raises — disclosure must not break
    ingress.
    """
    try:
        payload = getattr(event, 'payload', None)
        idx = (msg.metadata or {}).get('channel')
        name = channel_name_for(handler, idx) or '?'
        text = msg.content or ''
        reach = reach_of(payload)
        n = getattr(handler, '_ingress_keys_logged', 0)
        if n < 3:
            handler._ingress_keys_logged = n + 1
            keys = (sorted(payload.keys()) if isinstance(payload, dict)
                    else type(payload).__name__)
            logger.info(
                f"MeshCore channel rx idx={idx} name={name} {reach} keys={keys} text={text[:40]!r}")
        else:
            logger.info(f"MeshCore channel rx idx={idx} name={name} {reach} text={text[:40]!r}")
    except Exception as e:
        logger.debug(f"channel ingress disclosure failed: {e}")


# --------------------------------------------------------------------------- #
# Inbound channel POLICY — built on the slot INDEX, never on message text.    #
# --------------------------------------------------------------------------- #

def resolve_bridge_channels(meshcore_config: Any) -> Tuple[Optional[frozenset], str]:
    """Resolve the inbound source-slot allowlist → ``(allowed, source)``.

    ``allowed`` is a frozenset of permitted slot indices, or ``None`` meaning
    "everything except Public (slot 0)". ``source`` names where the posture
    came from (``env`` / ``config`` / ``default``) so the startup line can
    say it. An explicit EMPTY list means "bridge nothing" and is honoured —
    refusing everything is a legitimate posture for a DM-only box, and
    silently reading it as "allow all" is the degraded-value-looks-valid
    class this tree exists to refuse. A token that is not an int is logged
    and ignored: a typo must neither narrow nor widen the gate quietly.
    """
    raw = os.environ.get(BRIDGE_CHANNELS_ENV)
    source = "env"
    if raw is None and meshcore_config is not None:
        declared = getattr(meshcore_config, 'bridge_source_channels', None)
        if declared is not None:
            raw = ",".join(str(t) for t in declared)
            source = "config"
    if raw is None:
        return None, "default"
    out = set()
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.add(int(tok))
        except ValueError:
            logger.warning(
                f"MeshCore inbound allowlist token {tok!r} ({source}) is not a "
                f"slot index; ignored — the gate is unchanged by it")
    return frozenset(out), source


def channel_name_for(handler: Any, idx: Optional[int]) -> Optional[str]:
    """The DEVICE's name for slot ``idx`` (from the cached CHANNEL_INFO table
    the radio reported at connect), or None when unknown. Never from text."""
    if idx is None:
        return None
    try:
        state = handler.get_radio_state(refresh=False) or {}
        for ch in state.get("channels") or ():
            if ch.get("idx") == idx:
                name = (ch.get("name") or "").strip()
                return name or None
    except Exception as e:  # a missing table is "unknown", never a crash
        logger.debug(f"channel_name_for({idx}) failed: {e}")
    return None


class InboundChannelPolicy:
    """ONE predicate for both inbound channel legs (event + poll).

    Born 2026-09-18 from the review of the incident of the same day: an
    index-based guard shipped on a field that was always 0 and refused 100%
    of traffic. This one reads ``metadata['channel']``, which ``from_meshcore``
    now fills from the wire's ``channel_idx``. Every verdict is legible in the
    journal: slot, device name, verdict, reason, and the setting that changes
    it — so a later reader (human or model) learns the posture from the log
    rather than from the code.
    """

    def __init__(self, meshcore_config: Any = None) -> None:
        self.allowed_channels, self.source = resolve_bridge_channels(meshcore_config)
        self._lock = threading.Lock()
        self.suppressed = 0

    # -- posture ---------------------------------------------------------
    def describe(self) -> str:
        if self.allowed_channels is None:
            allow = f"all slots except Public({MESHCORE_PUBLIC_CHANNEL})"
        elif not self.allowed_channels:
            allow = "NOTHING (empty allowlist — DM-only posture)"
        else:
            allow = "slots " + ",".join(str(i) for i in sorted(self.allowed_channels))
        return f"allow={allow} source={self.source}"

    def log_posture(self, handler: Any = None) -> None:
        """One INFO line at connect: what bridges, why, and how to change it."""
        table = " device-channels=(not yet read)"
        try:
            chans = (handler.get_radio_state(refresh=False) or {}).get("channels") or ()
            if chans:
                table = " device-channels=" + " ".join(
                    f"{c.get('idx')}={c.get('name') or '?'}" for c in chans)
        except Exception:
            pass
        logger.info(
            f"MeshCore inbound channel policy: {self.describe()}{table} — "
            f"set {BRIDGE_CHANNELS_ENV}=<idx,idx> (or meshcore.bridge_source_channels) "
            f"to change; Public(0) is refused unless listed")

    # -- the predicate ---------------------------------------------------
    def verdict(self, msg: Any) -> Tuple[bool, Optional[int], str]:
        """``(allowed, slot, reason)`` for one inbound channel message."""
        slot = (getattr(msg, 'metadata', None) or {}).get('channel')
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            # No slot on the wire, or garbage: unknown is not known-safe.
            return False, None, "slot unknown (payload named no channel_idx) — unknown is not Public and not private; refused"
        if self.allowed_channels is None:
            if slot == MESHCORE_PUBLIC_CHANNEL:
                return False, slot, "Public is refused by default (no allowlist declared)"
            return True, slot, "not Public; default posture bridges every other slot"
        if slot in self.allowed_channels:
            return True, slot, f"slot listed in allowlist ({self.source})"
        return False, slot, f"slot not in allowlist {sorted(self.allowed_channels)} ({self.source})"

    def bridge_allowed(self, msg: Any) -> bool:
        return self.verdict(msg)[0]

    def note_suppressed(self, msg: Any, name: Optional[str], reason: str) -> int:
        """Record + disclose a refusal. Never silent; returns the new count."""
        with self._lock:
            self.suppressed += 1
            n = self.suppressed
        slot = (getattr(msg, 'metadata', None) or {}).get('channel')
        # EVERY refusal at INFO. The first cut backed off to DEBUG after three,
        # and within an hour (2026-09-18 13:13) a reader saw an ingress line
        # with no verdict after it — the exact silence this disclosure exists
        # to refuse. LoRa channel rates cannot flood a journal; the metrics
        # line carries the running count regardless.
        logger.info(
            f"MeshCore inbound REFUSED slot={slot} name={name or '?'}: {reason}; "
            f"message not bridged (suppressed={n}) — set {BRIDGE_CHANNELS_ENV} to change")
        return n


def apply_inbound_policy(handler: Any, msg: Any, dev_name: Optional[str] = None) -> bool:
    """Run the handler's inbound policy on ``msg``; on refusal, disclose it,
    bump the stats witness and return False. ONE call for both legs."""
    policy = handler._inbound_policy
    allowed, slot, reason = policy.verdict(msg)
    if allowed:
        return True
    if dev_name is None:
        dev_name = channel_name_for(handler, slot)
    policy.note_suppressed(msg, dev_name, reason)
    with handler._stats_lock:
        handler.stats['meshcore_channel_suppressed'] = (
            handler.stats.get('meshcore_channel_suppressed', 0) + 1)
    return False


def format_channel_metrics(m: Dict[str, Any], policy: InboundChannelPolicy) -> Optional[str]:
    """The periodic dual-path + policy witness line, or None when nothing
    has been received yet (an empty line would read as health)."""
    total = m['event_received'] + m['poll_discovered']
    if total == 0:
        return None
    event_pct = m['event_received'] / total * 100
    miss_pct = m['event_missed'] / total * 100
    return (
        f"MeshCore channel metrics: "
        f"event={m['event_received']} ({event_pct:.0f}%), "
        f"poll_discovered={m['poll_discovered']}, "
        f"event_missed={m['event_missed']} ({miss_pct:.0f}%), "
        f"reconciled={m['duplicate_reconciled']}, "
        f"poll_cycles={m['poll_cycles']}, "
        f"suppressed={policy.suppressed} ({policy.describe()})"
    )
