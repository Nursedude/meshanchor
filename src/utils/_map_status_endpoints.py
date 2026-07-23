"""Status endpoint mixin for :class:`MapRequestHandler`.

Holds the ``/api/status`` surface — the server health rollup that the
federation poll cycle and dashboards consume — plus its private reader:

- ``_serve_status``             — ``/api/status`` (history/directory stats,
                                  response-cache stats blocks, radio)
- ``_get_radio_status_summary`` — TCP/USB radio connectivity summary
                                  (MeshCore-aware: excludes the device
                                  claimed via /dev/ttyMeshCore)

The cache-stats shapes surfaced under ``status["geojson"]["cache"]``,
``status["topology"]["cache"]`` and ``status["directory_cache"]["cache"]``
are test-pinned (``tests/test_response_byte_cache.py``) — do not alter
them here.

Extracted from ``map_http_handler.py`` to keep that file under the
1,500-line size cap (``CLAUDE.md``). No behaviour change — methods are
mixed into ``MapRequestHandler`` via inheritance.

Mirrors MeshForge's ``_map_status_endpoints.py`` boundary (MA's status
block is leaner — no watchdog/mini-dudeai/radio-config stitches yet).
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from utils.safe_import import safe_import

_get_connection_manager, _ConnectionMode, _HAS_MESHTASTIC_CONN = safe_import(
    'utils.meshtastic_connection', 'get_connection_manager', 'ConnectionMode'
)

MINI_STALE_S = 300.0  # 5 min — 10x mini-dudeai's 30s tick (SSOT for the block)


def mini_block_from_payload(
    payload: Any,
    *,
    now: Optional[float] = None,
    stale_after_s: float = MINI_STALE_S,
) -> Dict[str, Any]:
    """Shape a raw mini ``state.json`` payload into the
    ``/api/status.mini_dudeai`` block, re-deriving staleness at read time.

    Ported from MeshForge's ``mini_block_from_payload`` so the MF fleet-truth
    classifier (which reads THIS box's ``/api/status.mini_dudeai``) sees an
    identical shape: a stale payload reads ``ok=False`` with a ``stale:``
    reason, which the classifier maps to DARK — never green-with-old-numbers.
    """
    if not isinstance(payload, dict):
        return {"installed": True, "ok": False,
                "reason": "malformed_json: not an object"}

    ts = payload.get("last_tick_ts")
    age_s: Optional[float] = None
    if isinstance(ts, (int, float)):
        age_s = max(0.0, (now if now is not None else time.time()) - float(ts))
    stale = bool(age_s is not None and age_s > stale_after_s)

    rules = payload.get("rules") or {}
    active = [
        {"rule_id": rs.get("rule_id"), "subject": rs.get("subject"),
         "detail": rs.get("last_detail", "")}
        for rs in rules.values()
        if isinstance(rs, dict) and rs.get("currently_active")
    ]
    top = sorted(
        ({"rule_id": rs.get("rule_id"), "subject": rs.get("subject"),
          "fire_count_24h": rs.get("fire_count_24h", 0)}
         for rs in rules.values()
         if isinstance(rs, dict) and rs.get("fire_count_24h")),
        key=lambda r: r["fire_count_24h"], reverse=True,
    )[:5]

    block = {
        "installed": True,
        "ok": not stale,
        "ts": ts,
        "last_tick_iso": payload.get("last_tick_iso"),
        "age_s": age_s,
        "host": payload.get("host"),
        "rule_count": payload.get("rule_count"),
        "error_count": payload.get("error_count"),
        "active_rules": active,
        "top_rules_24h": top,
    }
    if stale:
        block["reason"] = (
            f"stale: last tick {age_s:.0f}s ago "
            f"(threshold {stale_after_s:.0f}s) — mini-dudeai "
            f"daemon may have crashed"
        )
    return block


def read_ma_mini_state_block() -> Dict[str, Any]:
    """Stitch the MeshAnchor mini's live state into ``/api/status.mini_dudeai``.

    MA runs its OWN mini-dudeai sub-agent (preset ``meshanchor_fleet``),
    namespaced under ``ma_mini_dir`` so it never collides with a co-located
    MeshForge mini twin on a dual-stack box. Resolve the state path via the
    SAME ``ma_mini_dir()`` the mini and ``fleet_watchdog._mini_dead_reason``
    use (one constant, honest_failure_modes #5) — a hardcoded path here would
    silently drift from where the daemon writes.

    Degrades to ``installed:False`` ONLY when the state file is genuinely
    absent (mini not seeded on this box) — it never asserts absence while the
    daemon is running (the stale-stub bug this replaced, 2026-07-22).
    """
    try:
        from mini_dudeai.presets.meshanchor_fleet import ma_mini_dir
        from mini_dudeai._util import operator_home
        path = os.path.join(ma_mini_dir(operator_home()), "state.json")
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return {"installed": False, "reason": "no_state_file (mini not seeded here)"}
    except OSError as exc:
        return {"installed": False, "reason": f"read_error: {exc}"}
    except Exception as exc:  # noqa: BLE001 — mini pkg import failure ≠ absence
        return {"installed": False, "reason": f"mini_unavailable: {exc}"}

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return {"installed": True, "ok": False, "reason": f"malformed_json: {exc}"}
    return mini_block_from_payload(payload)


def _read_deployment_role(app_name: str) -> Optional[str]:
    """Best-effort role for the app-identity block: ``~/.config/<app>/
    deployment.json`` -> ``"role"``. Returns None if absent/unreadable — a box
    may run the app role-less, and absence must never become a false role claim
    (honest_failure_modes #2: unobservable != a value)."""
    try:
        from utils.paths import get_real_user_home
        p = get_real_user_home() / ".config" / app_name / "deployment.json"
        if not p.exists():
            return None
        role = json.loads(p.read_text()).get("role")
        return role if isinstance(role, str) and role else None
    except Exception:
        return None


def _build_app_block() -> Dict[str, Any]:
    """Self-identifying ``app`` block for ``/api/status`` — cross-domain fleet
    presence, Layer 0 (``.claude/plans/cross_domain_fleet_presence_design_2026_06_23.md``).

    MeshForge and MeshAnchor both serve an identically-shaped ``/api/status`` on
    ``:5000``, so a cross-domain probe cannot tell whose endpoint it hit without
    this — the 2026-06-23 misread where MeshAnchor's ``honest_status`` reported
    MeshForge's ``confirmation_rate`` as its own. The per-app identity falls out
    of each repo's own ``src/__version__.py``, so this function is carried
    BYTE-IDENTICAL across both repos (``parity_check.py``) with no per-app edits.
    Pure-stdlib, best-effort: every field degrades to a present, honest value
    rather than raising and dropping the whole ``/api/status`` payload.
    """
    import socket
    name = "unknown"
    version = "unknown"
    try:
        from __version__ import __app_name__, __version__ as _ver
        name = (__app_name__ or "unknown").strip().lower() or "unknown"
        version = _ver or "unknown"
    except Exception:
        pass
    try:
        host = socket.gethostname() or "unknown"
    except Exception:
        host = "unknown"
    block: Dict[str, Any] = {
        "name": name,        # "meshforge" | "meshanchor" — the disambiguation key
        "version": version,
        "repo": name,        # on this fleet the repo basename matches the app name
        "host": host,
    }
    role = _read_deployment_role(name)
    if role:
        block["role"] = role
    return block


FLEET_WATCHDOG_UNIT = "meshanchor-fleet-watchdog"


def watchdog_block_from_blackouts(*, now: Optional[float] = None) -> Dict[str, Any]:
    """MA-native ``/api/status.watchdog`` block from the blackout organ.

    Speaks the fleet-truth block contract ({installed, ok, reason, signals,
    coverage}) in MeshAnchor's OWN idiom: the fleet-watchdog's blackout KINDS
    are MA's closed signal enum, active blackout rows are the signals, and
    coverage marks every kind clean/active per read.

    Honesty guards (the honest_status precedent — an empty blackout table
    must never read green on its own):
    - organ liveness comes from the UNIT state (a dead fleet-watchdog can't
      record blackouts): not-installed → installed:false (benign absence);
      inactive → ok:false with a real-fault reason (FAILED downstream);
      state check itself failing → an "unobservable" reason (DARK downstream,
      never healthy and never an invented fault).
    - the blackout store failing to read → "unobservable" reason (DARK).
    - RESIDUAL (accepted, same as honest_status): a wedged-but-active
      watchdog process serving stale rows is invisible to the unit check.
    """
    import time as _time
    wall_now = now if now is not None else _time.time()

    try:
        from utils.service_check import ServiceState, check_service
        st = check_service(FLEET_WATCHDOG_UNIT)
    except Exception as exc:  # service layer itself broken — cannot observe
        return {"installed": True, "ok": False,
                "reason": f"watchdog unit state unobservable: {exc}"}

    if st.state == ServiceState.NOT_INSTALLED:
        return {"installed": False,
                "reason": "fleet-watchdog unit not installed on this box"}
    if not st.available:
        return {"installed": True, "ok": False,
                "reason": "fleet-watchdog unit inactive — blackout detection down"}

    try:
        from monitoring.fleet_history import query_active_blackouts
        active_rows = query_active_blackouts()
    except Exception as exc:
        return {"installed": True, "ok": False,
                "reason": f"blackout store unobservable: {exc}"}

    from monitoring.fleet_watchdog import ALL_KINDS, KIND_ROLE_DRIFT
    signals = []
    for r in active_rows:
        if not isinstance(r, dict) or not r.get("kind"):
            continue
        signals.append({
            "class": r["kind"],
            "subject": "fleet",
            "severity": "degraded" if r["kind"] == KIND_ROLE_DRIFT else "wedge",
            "detail": r.get("reason") or "",
            "first_seen": r.get("ts_started"),
        })
    active_kinds = {s["class"] for s in signals}
    coverage = {
        k: ({"disp": "active"} if k in active_kinds else {"disp": "clean"})
        for k in ALL_KINDS
    }
    return {
        "installed": True,
        "ok": not signals,
        "ts": wall_now,
        "age_s": 0.0,
        "probe_count": len(ALL_KINDS),
        "signals": signals,
        "coverage": coverage,
    }


class StatusEndpointsMixin:
    """``/api/status`` endpoint + its radio connectivity reader."""

    def _serve_status(self):
        """Serve server status including radio connection info."""
        status = {
            "status": "running",
            "time": datetime.now().isoformat(),
            "collector": self.collector is not None,
        }

        # App self-identification (cross-domain fleet presence, Layer 0): name
        # the app + version so a probe knows WHICH NOC answered on :5000 —
        # MeshForge and MeshAnchor share this endpoint's shape. Emitted before
        # the collector-gated blocks so a warming/degraded server still
        # self-identifies.
        status["app"] = _build_app_block()

        # Include history stats if available
        if self.collector and self.collector._history:
            try:
                status["history"] = self.collector._history.get_stats()
            except Exception:
                status["history"] = None

            # Directory stats (Issue #49) — persistent per-node cache
            # across protocols, with tiered retention. Surfaces total
            # count, by-network, by-source-origin, last-seen range so
            # operators can see at a glance how many MeshCore/AREDN/RNS
            # nodes are cached and which retention tier they fall into.
            try:
                status["directory"] = self.collector._history.get_directory_stats()
            except Exception as e:
                logger.debug(f"directory stats lookup failed: {e}")
                status["directory"] = None

        # Include radio connection status
        status["radio"] = self._get_radio_status_summary()

        # Cross-domain observability blocks (fleet-truth arc, 2026-07-19).
        # Both NOCs' /api/fleet/truth builders read these; without them every
        # MA box's watchdog/mini cells read unobservable-DARK and taint the
        # box roll-up. The watchdog block speaks MA's NATIVE organ (blackout
        # kinds), not a clone of MF's probe watchdog. The mini block reports
        # MA's OWN mini-dudeai sub-agent (preset meshanchor_fleet, twinned
        # 2026-07-22); before that twinning this was a hardcoded installed:false
        # stub ("MeshAnchor has no local sub-agent"), which went stale the
        # moment the mini went live and rendered a running daemon as absent-dark
        # on both NOCs — the read now observes the real state.json.
        status["watchdog"] = watchdog_block_from_blackouts()
        status["mini_dudeai"] = read_ma_mini_state_block()

        # Response-cache observability (Issues #70/#71). Surfaces hit/miss/
        # coalesced counts per heavy endpoint so operators can confirm the
        # caches are coalescing work (and spot a regression where an endpoint
        # stops calling get_or_build, or a TTL too short to coalesce).
        if self.collector:
            for stat_key, attr in (
                ("geojson", "_geojson_response_cache"),
                ("topology", "_topology_response_cache"),
                ("directory_cache", "_directory_response_cache"),
            ):
                try:
                    cache = getattr(self.collector, attr, None)
                    if cache is not None:
                        status[stat_key] = {
                            "cache": {**cache.stats(), "ttl_s": cache.ttl_s}
                        }
                except Exception as e:
                    logger.debug(f"{stat_key} cache stats lookup failed: {e}")

        data = json.dumps(status).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self._send_cors_header()
        self.end_headers()
        self.wfile.write(data)

    def _get_radio_status_summary(self) -> Dict[str, Any]:
        """Get a summary of radio connection status for the status endpoint."""
        if not _HAS_MESHTASTIC_CONN:
            return {"available": False, "error": "meshtastic library not installed"}

        # Check TCP port (meshtasticd)
        tcp_available = False
        try:
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                tcp_available = sock.connect_ex(('localhost', 4403)) == 0
        except Exception as e:
            logger.debug(f"TCP port check failed: {e}")

        # Check USB serial device. Exclude any device claimed by MeshCore
        # via the persistent /dev/ttyMeshCore symlink (created by
        # scripts/99-meshcore.rules). Without this filter, on a MeshCore-
        # primary host the glob returns the RAK4631's /dev/ttyACM0 and we
        # report mode='serial' as if a Meshtastic radio were attached.
        import glob
        import os
        excluded: set = set()
        if os.path.exists('/dev/ttyMeshCore'):
            try:
                excluded.add(os.path.realpath('/dev/ttyMeshCore'))
            except OSError:
                pass
        usb_devices = [
            d for d in (glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*'))
            if os.path.realpath(d) not in excluded
        ]
        usb_available = len(usb_devices) > 0

        # Determine connection mode
        if tcp_available:
            mode = "tcp"
            connected = True
        elif usb_available:
            mode = "serial"
            connected = True
        else:
            mode = "none"
            connected = False

        return {
            "connected": connected,
            "mode": mode,
            "tcp_available": tcp_available,
            "usb_available": usb_available,
            "usb_devices": usb_devices if usb_available else [],
        }
