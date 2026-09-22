"""HTTP handler for the daemon's ``/api/stats`` endpoint.

Mirrors `utils.lxmf_broadcast_api` and `utils.radio_api`. Exposes the
in-process `RNSMeshtasticBridge.stats` dict plus a few derived shape
fields (uptime, connection booleans, bridge_status) so operators can
observe privacy-class drop counters without parsing journalctl.

Routes (served on :8081 by ConfigAPIServer):
    GET /api/stats           localhost-only

Localhost-only because the counters include per-destination message
volumes and internal failure modes that are passive-attacker assets
(matches the existing `/metrics` gate). LAN-readable diagnostic
surface is `/health` — coarse-grained service state, no counters.

The handler argument is a live `ConfigAPIHandler` instance — we just
need its I/O surface (``path``, ``_send_json``, ``_send_error_json``,
``_check_localhost``). Typed as ``Any`` so this module has no
circular dependency on ``utils.config_api``.

Counter surface (selected, not exhaustive — the full live dict is
returned so any future counter shows up automatically):

  meshcore_bridge_default_channel_drop  — Issue #37 privacy refusal.
      Non-zero means cross-protocol bridge cargo arrived without a
      resolvable target slot and was DROPPED rather than leaked to
      MeshCore Public (slot 0). Operator fix: set
      ``config.meshcore.bridge_target_channel`` to a valid slot.

  meshcore_dm_dropped_contact_not_found  — Issue #35 privacy refusal.
      DM contact resolution failed; message dropped rather than
      cascaded to a channel broadcast.

  messages_{src}_to_{dst}  — successful cross-protocol bridges.
  errors / bounced / meshcore_tx / meshcore_rx — bridge-wide totals.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _get_active_bridge(handler: Any):
    """Return the active RNSMeshtasticBridge or None (with HTTP error sent)."""
    try:
        from gateway.gateway_cli import get_active_bridge
    except ImportError:
        handler._send_error_json(503, "Gateway bridge module not loaded")
        return None
    bridge = get_active_bridge()
    if bridge is None:
        handler._send_error_json(503, "Gateway bridge not active")
        return None
    return bridge


def _oracle_posture(meshcore_handler) -> dict:
    """The oracle leg's posture, as the DAEMON actually built it.

    Why this is published by the daemon instead of computed in the TUI:
    the posture is decided by env vars in the DAEMON's process — on
    meshanchor-server the allowlist comes from a systemd drop-in. A TUI
    reading its own ``os.environ`` would report the posture of whichever
    shell launched the TUI, confidently and wrongly. Only the process that
    built the responder can say what it built (calibrated_claims rule 7:
    verify the consumer-of-record, not a proxy for it).

    Three outcomes, deliberately distinct — collapsing any pair of them
    would report a broken or unobservable oracle as a deliberately
    disabled one:

      observable=False   no meshcore handler on this bridge; we cannot
                         see, which is UNKNOWN and never "off"
      enabled=False + error   the oracle was asked for and FAILED to build
      enabled=False           off by design (the default; the env is unset)
    """
    if meshcore_handler is None:
        return {"observable": False,
                "reason": "no meshcore handler on this bridge"}
    err = getattr(meshcore_handler, "_oracle_error", None)
    if err:
        return {"observable": True, "enabled": False, "error": err}
    oracle = getattr(meshcore_handler, "_oracle", None)
    if oracle is None:
        return {"observable": True, "enabled": False}
    # Count, never the tokens: the roadmap asks for a count, and the
    # allowlist holds node keys that do not need a wider audience than
    # the box already gives them.
    return {
        "observable": True,
        "enabled": True,
        "answer_all": bool(getattr(oracle, "answer_all", False)),
        "allowlist": len(getattr(oracle, "allowlist", ()) or ()),
        "channels": sorted(getattr(oracle, "allowed_channels", ()) or ()),
        "cooldown_s": getattr(oracle, "cooldown_s", None),
        "consume": bool(getattr(oracle, "consume", False)),
        "transport": getattr(oracle, "transport", None),
    }


def handle_get(handler: Any) -> None:
    """Serve GET /api/stats — localhost-only.

    Returns the live ``bridge.stats`` dict plus derived shape fields
    (uptime, mesh/rns/meshcore connection booleans). 503 when the
    bridge isn't started yet — operator can poll the endpoint during
    daemon startup and the JSON error explains the gating state.
    """
    if not handler._check_localhost():
        handler._send_error_json(403, "Forbidden — localhost only")
        return

    bridge = _get_active_bridge(handler)
    if bridge is None:
        return

    try:
        # Copy under the bridge's stats lock so a concurrent counter
        # increment can't tear the snapshot. The bridge already does
        # ``self.stats.copy()`` inside get_status — but get_status
        # builds a richer dict than we need and adds health.get_summary
        # which can transiently raise during disconnect. Reach the
        # stats dict directly with the lock that protects it.
        lock = getattr(bridge, "_stats_lock", None)
        if lock is not None:
            with lock:
                stats_snapshot = dict(bridge.stats)
        else:
            stats_snapshot = dict(bridge.stats)
    except Exception as e:
        handler._send_error_json(500, f"Bridge stats read failed: {e}")
        return

    # start_time is a datetime — JSON-serialize as ISO + seconds-uptime
    start_time = stats_snapshot.pop("start_time", None)
    uptime_seconds = None
    start_time_iso = None
    if start_time is not None:
        try:
            uptime_seconds = (datetime.now() - start_time).total_seconds()
            start_time_iso = start_time.isoformat()
        except Exception:
            uptime_seconds = None
            start_time_iso = None

    # Shape fields — same booleans `/health` derives, exposed here so
    # the operator gets stats + state in one fetch.
    mesh_handler = getattr(bridge, "_mesh_handler", None)
    meshcore_handler = getattr(bridge, "_meshcore_handler", None)

    # LXMF->MeshCore re-emit bridge stats — loop-guard visibility
    # (filtered_nested_bridge counts dropped echoes). None when re-emit
    # is not configured.
    reemit = getattr(bridge, "_meshtastic_reemit", None)
    reemit_stats = None
    if reemit is not None:
        try:
            rlock = getattr(reemit, "_stats_lock", None)
            if rlock is not None:
                with rlock:
                    reemit_stats = dict(reemit.stats)
            else:
                reemit_stats = dict(reemit.stats)
        except Exception:
            reemit_stats = None

    payload = {
        "oracle": _oracle_posture(meshcore_handler),
        "running": bool(getattr(bridge, "_running", False)),
        "uptime_seconds": uptime_seconds,
        "start_time": start_time_iso,
        "meshtastic_connected": bool(
            mesh_handler.is_connected if mesh_handler is not None else False
        ),
        "rns_connected": bool(getattr(bridge, "_connected_rns", False)),
        "meshcore_connected": bool(
            meshcore_handler.is_connected if meshcore_handler is not None else False
        ),
        "stats": stats_snapshot,
        "reemit": reemit_stats,
    }
    handler._send_json(payload)
