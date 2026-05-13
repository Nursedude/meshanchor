"""HTTP handlers for the daemon's `/lxmf-broadcast/*` endpoints.

Mirrors `utils.radio_api`. Surfaces the in-process LXMFBroadcastBridge
to the TUI (and other LAN consumers) without letting them touch the
subscriber SQLite directly — the bridge's `SubscriberStore` holds a
`threading.Lock` that only in-process consumers can honor.

Routes (all served on :8081 by ConfigAPIServer):
    GET    /lxmf-broadcast/status                LAN-readable
    GET    /lxmf-broadcast/subscribers           LAN-readable
    POST   /lxmf-broadcast/subscribers           localhost-only (actuates fan-out)
    DELETE /lxmf-broadcast/subscribers/<hash>    localhost-only (prune stale row)

The handler argument is a live `ConfigAPIHandler` instance — we just need
its I/O surface (`path`, `_send_json`, `_send_error_json`, `_read_body`).
Typed as `Any` so this module has no circular dependency on config_api.
"""

from __future__ import annotations

import re
from typing import Any

_LXMF_HASH_RE = re.compile(r"^[0-9a-fA-F]{16,32}$")


def _get_active(handler: Any):
    """Return the active LXMFBroadcastBridge or None (with HTTP error sent)."""
    try:
        from gateway.lxmf_broadcast_bridge import get_active_broadcast_bridge
    except ImportError:
        handler._send_error_json(503, "LXMF broadcast bridge module not loaded")
        return None
    bridge = get_active_broadcast_bridge()
    if bridge is None:
        handler._send_error_json(
            503, "LXMF broadcast bridge not active"
        )
        return None
    return bridge


def handle_get(handler: Any) -> None:
    """Serve GET /lxmf-broadcast/status and /lxmf-broadcast/subscribers.

    Status always returns 200 once the bridge object exists, even when the
    bridge is still bootstrapping (running=False) — the operator wants to
    see the pre-ready state in the TUI, not a generic 503.
    """
    bridge = _get_active(handler)
    if bridge is None:
        return

    path = handler.path.split("?", 1)[0].rstrip("/")
    try:
        status = bridge.get_status()
    except Exception as e:
        handler._send_error_json(500, f"Bridge status read failed: {e}")
        return

    if path == "/lxmf-broadcast/status":
        handler._send_json(status)
        return
    if path == "/lxmf-broadcast/subscribers":
        subs = status.get("subscribers", [])
        handler._send_json({"count": len(subs), "subscribers": subs})
        return

    handler._send_error_json(404, f"Unknown lxmf-broadcast path: {handler.path}")


def handle_post(handler: Any) -> None:
    """Serve POST /lxmf-broadcast/subscribers — add a local LXMF identity.

    Localhost-only (the caller in config_api gates this). The bridge's
    SubscriberStore.add() is idempotent — re-posting the same hash returns
    `added: false`. Returns `total` so the TUI can show the new count.
    """
    bridge = _get_active(handler)
    if bridge is None:
        return

    path = handler.path.split("?", 1)[0].rstrip("/")
    if path != "/lxmf-broadcast/subscribers":
        handler._send_error_json(
            404, f"Unknown lxmf-broadcast path: {handler.path}"
        )
        return

    body = handler._read_body()
    if not isinstance(body, dict):
        handler._send_error_json(400, "Body must be JSON object")
        return

    raw_hash = body.get("lxmf_hash")
    if not isinstance(raw_hash, str) or not _LXMF_HASH_RE.match(raw_hash.strip()):
        handler._send_error_json(
            400,
            "`lxmf_hash` must be a 16-32 char hex string",
        )
        return

    lxmf_hash = raw_hash.strip().lower()
    try:
        added = bridge._subs.add(lxmf_hash)
        total = len(bridge._subs.list_all())
    except Exception as e:
        handler._send_error_json(500, f"Subscriber add failed: {e}")
        return

    handler._send_json(
        {"added": bool(added), "lxmf_hash": lxmf_hash, "total": total}
    )


def handle_delete(handler: Any) -> None:
    """Serve DELETE /lxmf-broadcast/subscribers/<hash> — prune a subscriber.

    Localhost-only (the caller in config_api gates this). Operator surfaces
    stale subscribers via the TUI; this endpoint is what the prune button
    calls. Idempotent — removing an absent hash returns `removed: false`.
    """
    bridge = _get_active(handler)
    if bridge is None:
        return

    path = handler.path.split("?", 1)[0].rstrip("/")
    prefix = "/lxmf-broadcast/subscribers/"
    if not path.startswith(prefix):
        handler._send_error_json(404, f"Unknown lxmf-broadcast path: {handler.path}")
        return
    raw_hash = path[len(prefix):]
    if not _LXMF_HASH_RE.match(raw_hash.strip()):
        handler._send_error_json(
            400, "`lxmf_hash` in URL must be a 16-32 char hex string"
        )
        return

    lxmf_hash = raw_hash.strip().lower()
    try:
        removed = bridge._subs.remove(lxmf_hash)
        total = len(bridge._subs.list_all())
    except Exception as e:
        handler._send_error_json(500, f"Subscriber remove failed: {e}")
        return

    handler._send_json(
        {"removed": bool(removed), "lxmf_hash": lxmf_hash, "total": total}
    )
