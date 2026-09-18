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
from typing import Any, Optional, Tuple

from .meshcore_bridge_mixin import _split_meshcore_channel_header

logger = logging.getLogger(__name__)


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
        text = msg.content or ''
        n = getattr(handler, '_ingress_keys_logged', 0)
        if n < 3:
            handler._ingress_keys_logged = n + 1
            keys = (sorted(payload.keys()) if isinstance(payload, dict)
                    else type(payload).__name__)
            logger.info(
                f"MeshCore channel rx idx={idx} keys={keys} text={text[:40]!r}")
        else:
            logger.info(f"MeshCore channel rx idx={idx} text={text[:40]!r}")
    except Exception as e:
        logger.debug(f"channel ingress disclosure failed: {e}")
