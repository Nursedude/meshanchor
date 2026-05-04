"""HTTP handler for the daemon's `/health` endpoint.

Phase 5 surface: profile-aware startup health snapshot. The TUI and external
clients (e.g. a status dashboard on another box) can hit this to learn what
state MeshAnchor is in WITHOUT firing false-red alarms when an Optional
Gateway (meshtasticd, rnsd) isn't configured.

Routes:
    GET /health        — JSON snapshot built from `utils.startup_health`

Behaviour mirrors `utils.radio_api`: the class method on `ConfigAPIHandler`
delegates here so `config_api.py` stays under the 1500-line cap.

The endpoint reads the active deployment profile via
`utils.deployment_profiles.load_or_detect_profile()` so the snapshot is
profile-aware. A MESHCORE-only deployment with no meshtasticd / rnsd
returns `overall_status="ready"` because both services are
`not_applicable` for that profile.
"""

from __future__ import annotations

from typing import Any


def handle_get(handler: Any) -> None:
    """Serve GET /health — profile-aware startup health snapshot.

    Returns `{"health": <dict>}` on success. Service degradation is
    encoded in the body, not the HTTP status — a 200 with
    `overall_status: "error"` is correct, because the daemon answered.

    503 only on import failures (the health module itself didn't load).
    500 on health-check exceptions (e.g. service_check raised).
    """
    try:
        from utils.startup_health import run_health_check, get_health_dict
    except ImportError:
        handler._send_error_json(503, "Health module not loaded")
        return

    profile = None
    try:
        from utils.deployment_profiles import load_or_detect_profile
        profile = load_or_detect_profile()
    except Exception:
        # Profile resolution is best-effort. If it fails we still want a
        # health snapshot — just without profile-aware classification.
        profile = None

    try:
        summary = run_health_check(profile=profile)
    except Exception as e:
        handler._send_error_json(500, f"Health check failed: {e}")
        return

    handler._send_json({"health": get_health_dict(summary)})
