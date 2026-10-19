"""MeshCore handler -> bridge ingress accounting (split out of
meshcore_handler.py, which sits at the MF025 1,500-line cap)."""

import logging
import time

logger = logging.getLogger("gateway.meshcore_handler")

# One WARNING per window for a full handler->bridge queue; DEBUG after.
INGRESS_FULL_WARN_WINDOW_S = 300.0


def note_ingress_queue_full(handler, path: str) -> None:
    """A MeshCore message lost before the bridge saw it (queue full): it is a
    message — counted (total + per path), WARNING once per window, never a
    per-message flood (MA sibling of MF ff94052a). ``path``: event | poll."""
    key = f"meshcore_ingress_dropped_queue_full_{path}"
    with handler._stats_lock:
        handler.stats["meshcore_ingress_dropped_queue_full"] = (
            handler.stats.get("meshcore_ingress_dropped_queue_full", 0) + 1)
        handler.stats[key] = handler.stats.get(key, 0) + 1
        total = handler.stats["meshcore_ingress_dropped_queue_full"]
    warn_ingress_full(handler, f"message dropped ({path} path; {total} so far)")


def warn_ingress_full(handler, what: str) -> None:
    """The one rate-limited voice for a full handler->bridge queue (messages
    AND [MC:reply] DM notices share the window): WARNING once per window,
    DEBUG after — a stalled bridge loop must not flood the journal."""
    now = time.monotonic()
    last = getattr(handler, "_ingress_full_warned_at", None)
    if last is None or now - last >= INGRESS_FULL_WARN_WINDOW_S:
        handler._ingress_full_warned_at = now
        logger.warning(
            f"MeshCore→bridge queue full — {what}; further ones at DEBUG for "
            f"{INGRESS_FULL_WARN_WINDOW_S:.0f}s. The bridge loop is not "
            f"keeping up")
    else:
        logger.debug(f"MeshCore→bridge queue full — {what}")
