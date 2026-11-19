"""CS-1 witness EXPORT: the observer's state, across the daemon -> TUI hop.

``observer_snapshot()`` lives on the MeshCore handler inside
meshanchor-daemon; the TUI is another process. The daemon already writes
``daemon_status.json`` every ``status_file_interval`` seconds, with
``services.gateway_bridge`` built by ``gateway_cli.get_gateway_stats()``.
This module is both halves of that hop:

- WRITER ``observer_status_for(bridge)`` — called by ``get_gateway_stats`` on
  every return path, so the snapshot is derived at status-write time from the
  live handler, never cached (the CS-1 seam: every surface an operator reads
  is derived from the one predicate, at read time).
- READER ``read_observer`` (one file read -> tone, line, raw export, context)
  and ``observer_status_line`` (its tone + line) — the TUI's one line.

The reader's seam (review r1 + r2 found the same kind twice — a verdict
computed without all of its context): ``describe`` derives EVERY context fact
first (the systemd unit, staleness, a future timestamp, the daemon's own
status, the bridge's liveness), and any fault prefixes whatever the observer
said. No observer verdict is ever rendered without them.

The two share ``STATUS_KEY`` (hfm #5). Every degraded read — no file, bad
JSON, a stale or future timestamp, a missing key, a state outside the
vocabulary — is UNKNOWN, never healthy (hfm #1, #2). ``ok`` means packets
were QUEUED to the local broker (paho rc 0); it is not proof CoreScope stored
them, and the line says so.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

STATUS_KEY = "meshcore_observer"

# Tone per state. Closed enum, closed consumer (hfm #7): a test enumerates
# every state derive_state() can return and fails if one has no tone here.
TONES: Dict[str, str] = {
    # observer's own states (gateway.meshcore_observer.derive_state)
    "disabled": "off",
    "never": "unknown",
    "ok": "ok",
    "refusing": "bad",
    "failing": "bad",
    "no_radio": "bad",
    "not_subscribed": "bad",
    "no_mqtt_client": "bad",
    "client_error": "bad",
    "subscribe_failed": "bad",
    "unsupported_supervisor_mode": "bad",
    # export-level states (this module's writer)
    "gateway_not_running": "unknown",
    "no_meshcore_handler": "unknown",
    "unobservable": "unknown",
    # disabled, but the configured value was not a JSON bool (D5)
    "disabled_config_error": "bad",
}

# The TUI menu subtitle line is cut here; the detail view shows the whole.
SUBTITLE_MAX = 76
_FALLBACK_INTERVAL_S = 30
# A written interval above this is not a cadence anyone meant (hfm #3); the
# reader falls back rather than widen its freshness window without bound.
MAX_INTERVAL_S = 3600

# A timestamp this far in the future is a clock step, not skew (hfm #6).
_FUTURE_TOLERANCE_S = 60.0


def _describe_exc(error: BaseException) -> str:
    try:
        return f"{type(error).__name__}: {error}"
    except Exception:
        return f"{type(error).__name__}: <unprintable>"


def observer_status_for(bridge: Any) -> Dict[str, Any]:
    """The observer's exported status for ``bridge``. Never raises; the
    result always survives ``json.dumps`` (the status writer catches only
    OSError)."""
    if bridge is None:
        return {"state": "gateway_not_running"}
    handler = getattr(bridge, "_meshcore_handler", None)
    if handler is None:
        return {"state": "no_meshcore_handler"}
    snap_fn = getattr(handler, "observer_snapshot", None)
    if not callable(snap_fn):
        return {"state": "unobservable",
                "reason": "MeshCore handler has no observer_snapshot"}
    try:
        snap = snap_fn()
    except Exception as e:
        return {"state": "unobservable",
                "reason": f"observer_snapshot raised: {_describe_exc(e)}"}
    if not isinstance(snap, dict) or not isinstance(snap.get("state"), str):
        return {"state": "unobservable",
                "reason": f"observer_snapshot returned {type(snap).__name__} "
                          f"without a state"}
    out = dict(snap)
    try:
        json.dumps(out)
    except Exception as e:  # TypeError, ValueError (cycle), RecursionError
        return {"state": "unobservable",
                "reason": f"observer snapshot is not JSON-serialisable: {_describe_exc(e)}"}
    return out


def _finite_number(v: Any) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:
        return False


def _valid_interval(v: Any) -> bool:
    return _finite_number(v) and 0 < v <= MAX_INTERVAL_S


def default_stale_after_s() -> float:
    """Three status-write intervals from the daemon's config — the FALLBACK
    when the status file does not say what interval its writer used."""
    try:
        from daemon_config import DaemonConfig
    except Exception:
        return 3 * float(_FALLBACK_INTERVAL_S)
    try:
        interval = DaemonConfig.load().status_file_interval
    except Exception:
        interval = DaemonConfig().status_file_interval
    if not _valid_interval(interval):
        interval = DaemonConfig().status_file_interval
    return 3 * float(interval)


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def _state_line(obs: Dict[str, Any], file_age_s: float) -> Tuple[str, str]:
    """(tone, line) for one exported observer object. Tone always comes from
    TONES, so the closed-enum test pins what is actually returned."""
    state = obs.get("state")
    if not isinstance(state, str) or state not in TONES or state == "disabled_config_error":
        return "unknown", f"UNKNOWN — unrecognised observer state {state!r}"
    if state == "disabled":
        err = obs.get("config_error")
        if err:
            return TONES["disabled_config_error"], f"OFF — config error: {err}"
        return TONES[state], "OFF (meshcore.observer_enabled = false)"
    if state == "ok":
        queued = obs.get("queued")
        if not isinstance(queued, int) or isinstance(queued, bool) or queued < 0:
            return "unknown", f"UNKNOWN — state ok without a valid queued count ({queued!r})"
        age = obs.get("last_queued_age_s")
        if age is None:
            last = ""
        elif _finite_number(age) and age >= 0:
            last = f"; last {_age(age + file_age_s)} ago"
        else:
            return "unknown", f"UNKNOWN — state ok with an invalid last_queued_age_s ({age!r})"
        # Disclaimer before the detail, so the 76-char subtitle keeps it.
        return TONES[state], f"ok — {queued} queued, storage unproven{last}"
    if state == "never":
        return TONES[state], "enabled, no packet seen yet"
    if state == "gateway_not_running":
        return TONES[state], "UNKNOWN — gateway bridge not running in the daemon"
    if state == "no_meshcore_handler":
        return TONES[state], "UNKNOWN — no MeshCore handler in the gateway"
    if state == "unobservable":
        return TONES[state], f"UNKNOWN — {obs.get('reason', 'no reason given')}"
    err = obs.get("last_error")
    return TONES[state], f"{state}" + (f" — {err}" if err else "")


@dataclass(frozen=True)
class ObserverView:
    """One read of daemon_status.json: the verdict, the raw export, and the
    context facts the verdict was derived from (for the detail view)."""
    tone: str
    line: str
    raw: Optional[Dict[str, Any]]
    raw_reason: str
    context: Dict[str, Any] = field(default_factory=dict)


def _view(doc: Any, *, now: datetime, stale_after_s: float,
          unit_state: Optional[str]) -> ObserverView:
    ctx: Dict[str, Any] = {"unit_state": unit_state}
    # A caller's fallback window must be a real, positive duration; NaN or
    # inf would mean never stale (review r3).
    if not (_finite_number(stale_after_s) and stale_after_s > 0):
        stale_after_s = 3 * float(_FALLBACK_INTERVAL_S)
    if not isinstance(doc, dict):
        return ObserverView("unknown", "UNKNOWN — daemon status is not a JSON object",
                            None, "daemon status is not a JSON object", ctx)
    services = doc.get("services")
    gb = services.get("gateway_bridge") if isinstance(services, dict) else None
    obs = gb.get(STATUS_KEY) if isinstance(gb, dict) else None
    if not isinstance(gb, dict):
        raw, raw_reason = None, "daemon status has no gateway_bridge entry"
    elif obs is None:
        raw, raw_reason = None, (f"gateway_bridge has no {STATUS_KEY} "
                                 f"(older daemon code, or a status error)")
    elif not isinstance(obs, dict):
        raw, raw_reason = None, f"{STATUS_KEY} is not an object"
    else:
        raw, raw_reason = obs, ""

    # --- context facts, ALL derived before any verdict ----------------------
    faults = []
    if unit_state is not None and unit_state != "active":
        faults.append(f"meshanchor-daemon unit is {unit_state!r}")
    try:
        updated = datetime.fromisoformat(str(doc.get("updated_at")))
    except ValueError:
        updated = None
    if updated is None:
        faults.append("daemon status has no valid updated_at")
    elif updated.tzinfo is None:
        faults.append("daemon status updated_at has no timezone")
    else:
        file_age = (now - updated).total_seconds()
        ctx["file_age_s"] = file_age
        daemon = doc.get("daemon")
        written = daemon.get("status_file_interval") if isinstance(daemon, dict) else None
        window = 3 * float(written) if _valid_interval(written) else stale_after_s
        if file_age < -_FUTURE_TOLERANCE_S:
            faults.append("daemon status timestamp is in the future (clock step?)")
        elif file_age > window:
            faults.append(f"STALE ({_age(file_age)} old)")
    daemon = doc.get("daemon")
    dstatus = daemon.get("status") if isinstance(daemon, dict) else None
    ctx["daemon_status"] = dstatus
    if dstatus != "running":
        faults.append(f"daemon status is {dstatus!r}, not running")
    if not isinstance(gb, dict):
        faults.append("daemon status has no gateway_bridge entry")
    else:
        ctx["bridge_running"] = gb.get("running")
        ctx["bridge_alive"] = gb.get("alive")
        if gb.get("running") is not True or gb.get("alive") is not True:
            faults.append(f"gateway bridge not running (running={gb.get('running')!r}, "
                          f"alive={gb.get('alive')!r}, status={gb.get('status')!r})")

    # --- the observer's own word, then the verdict --------------------------
    age_for_line = max(0.0, ctx.get("file_age_s", 0.0))
    if raw is not None:
        tone, said = _state_line(raw, age_for_line)
    elif obs is not None:
        tone, said = "unknown", "UNKNOWN — observer status is not an object"
    else:
        tone, said = "unknown", None
    if faults:
        line = "UNKNOWN — " + "; ".join(faults)
        if said:
            line += f"; last said: {said}"
        return ObserverView("unknown", line, raw, raw_reason, ctx)
    if said is None:
        return ObserverView("unknown",
                            "UNKNOWN — this daemon does not report the observer (older code?)",
                            raw, raw_reason, ctx)
    return ObserverView(tone, said, raw, raw_reason, ctx)


def describe(doc: Any, *, now: datetime, stale_after_s: float,
             unit_state: Optional[str] = None) -> Tuple[str, str]:
    """(tone, line) for a parsed daemon_status.json. tone: ok/off/bad/unknown.

    ``stale_after_s`` is the fallback; the writer's own
    ``daemon.status_file_interval`` wins when the file carries a sane one
    (hfm #5). ``unit_state`` is the systemd is-active word, when the caller
    has it: anything but ``active`` makes the file historical."""
    view = _view(doc, now=now, stale_after_s=stale_after_s, unit_state=unit_state)
    return view.tone, view.line


def _status_path() -> Path:
    from utils.paths import get_real_user_home
    return get_real_user_home() / ".config" / "meshanchor" / "daemon_status.json"


def read_observer(path: Optional[Path] = None, *,
                  now: Optional[datetime] = None,
                  stale_after_s: Optional[float] = None,
                  unit_state: Optional[str] = None) -> ObserverView:
    """ONE read of daemon_status.json -> the full view. Never raises."""
    ctx = {"unit_state": unit_state}
    try:
        if path is None:
            path = _status_path()
        if now is None:
            now = datetime.now(timezone.utc)
        if stale_after_s is None:
            stale_after_s = default_stale_after_s()
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        why = f"daemon status unreadable: {_describe_exc(e)}"
        return ObserverView("unknown", f"UNKNOWN — {why}", None, why, ctx)
    except Exception as e:
        return ObserverView("unknown", f"UNKNOWN — {_describe_exc(e)}", None,
                            _describe_exc(e), ctx)
    try:
        return _view(doc, now=now, stale_after_s=stale_after_s, unit_state=unit_state)
    except Exception as e:
        return ObserverView("unknown", f"UNKNOWN — {_describe_exc(e)}", None,
                            _describe_exc(e), ctx)


def observer_status_line(path: Optional[Path] = None, *,
                         now: Optional[datetime] = None,
                         stale_after_s: Optional[float] = None,
                         unit_state: Optional[str] = None) -> Tuple[str, str]:
    """(tone, line) of ``read_observer``. Never raises."""
    view = read_observer(path, now=now, stale_after_s=stale_after_s,
                         unit_state=unit_state)
    return view.tone, view.line


def raw_observer_export_with_reason(
        path: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """(exported observer object, "") or (None, why)."""
    view = read_observer(path)
    return view.raw, view.raw_reason


def raw_observer_export(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The exported observer object as written, or None."""
    return raw_observer_export_with_reason(path)[0]
