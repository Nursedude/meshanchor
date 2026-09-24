"""Delivery view — the domain's END (a message arrives) as one local screen.

MeshAnchor port of MeshForge's ``utils/delivery_view.py`` (2026-09-23,
MF ``00eb2b08`` + the Fable review fixes ``8c41765a``). Same contract: every
number names its source and age; tri-state per leg; the headline is never
better than its worst present leg.

Where MeshAnchor differs from MeshForge, and why:

* SOURCE. MeshForge reads files its gateway publishes. MeshAnchor's daemon
  publishes no snapshot file; its map serves ``/api/gateway/delivery`` and
  ``/api/gateway/queue`` straight from ``delivery_counters``. The TUI runs
  under sudo, and a root reader of that SQLite DB strands root-owned WAL/SHM
  in the operator's data dir (the #60 class) — so this reads the LOCAL MAP
  over HTTP, never the DB.
* ORGAN. The writer is ``meshanchor-daemon.service`` (it holds the DB open);
  the transport is ``meshanchor-map.service``. No daemon here (or stopped AND
  disabled) is inert; a daemon present with the map not answering is UNKNOWN.
* NO SOAK LEGS. MeshAnchor runs no synth / propagation soak exerciser, so
  there is nothing to render — an empty leg for an organ this app does not
  have would be noise, not honesty.

READ-ONLY and LOCAL-ONLY. It never opens the DB, never touches a service.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple
from urllib.error import URLError
from urllib.request import urlopen

from utils.active_health_checks_delivery import (
    DELIVERY_STALL_MIN_TERMINAL,
    confirmation_window,
)

OK = "ok"
INERT = "inert"
UNKNOWN = "unknown"

DAEMON_UNIT = "meshanchor-daemon.service"
MAP_UNIT = "meshanchor-map.service"
MAP_BASE = "http://127.0.0.1:5000"
HTTP_TIMEOUT_S = 5.0

#: The sister app's published delivery record (MeshForge's gateway writes it)
#: and its writer unit — presence + writer unit only, never opened as truth
#: for this screen (mirrors MF's PEER_DELIVERY_DBS in the other direction).
PEER_RECORDS = (
    ("MeshForge", os.path.join(".local", "share", "meshforge",
                               "delivery_snapshot.json"),
     "meshforge-gateway.service"),
)

_RATE_DEGRADED = 0.50


@dataclass
class Leg:
    title: str
    status: str
    source: str = ""
    age_s: Optional[float] = None
    why: str = ""
    lines: List[str] = field(default_factory=list)
    failing: bool = False
    # Fresh and not failing, but too little evidence to call it healthy.
    thin: bool = False


@dataclass
class DeliveryView:
    host: str
    now: float
    legs: List[Leg]
    peer_records: List[Tuple[str, str]] = field(default_factory=list)
    peer_leftovers: List[Tuple[str, str, float]] = field(default_factory=list)

    def headline(self) -> str:
        present = [leg for leg in self.legs if leg.status != INERT]
        if not present and self.peer_records:
            apps = ", ".join(sorted({a for a, _p in self.peer_records}))
            return (f"NOT READ HERE — no MeshAnchor delivery organ, but a "
                    f"{apps} delivery record may be live on this box; read it "
                    f"in {apps}'s own TUI.")
        if not present:
            return ("NO DELIVERY ORGAN HERE — meshanchor-daemon is not running "
                    "on this box by design (inert).")
        if any(leg.status == UNKNOWN for leg in present):
            return ("UNKNOWN — at least one delivery source here could not be "
                    "read; do not read this screen as the mesh being fine.")
        if any(leg.failing for leg in present):
            return "DEGRADED — a delivery source reports failures (see below)."
        if any(leg.thin for leg in present):
            return ("QUIET — every source answered and nothing is failing, but "
                    "too few recent confirmations to call delivery healthy.")
        return "Messages are arriving — every present source is fresh and passing."


# ── helpers ──────────────────────────────────────────────────────────────


def fmt_age(age_s: Optional[float]) -> str:
    if age_s is None:
        return "age ?"
    s = max(0, int(age_s))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    if s < 172800:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _num(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _fetch(path: str, fetcher=None) -> Tuple[Optional[dict], Optional[str]]:
    """``(payload, None)`` or ``(None, why)`` for a GET on the local map."""
    url = MAP_BASE + path
    try:
        if fetcher is not None:
            doc = fetcher(url)
        else:
            with urlopen(url, timeout=HTTP_TIMEOUT_S) as resp:
                doc = json.loads(resp.read())
    except (URLError, OSError, ValueError) as e:
        return None, f"the map did not answer {path} ({type(e).__name__})"
    except Exception as e:  # http.client.IncompleteRead is not an OSError
        return None, f"the map's {path} reply was unreadable ({type(e).__name__})"
    if not isinstance(doc, dict):
        return None, f"the map's {path} reply is not an object"
    if doc.get("error"):
        return None, f"the map reports {doc.get('error')}: {doc.get('reason', '')}".strip()
    return doc, None


def _unit_state(unit: str, resolver) -> str:
    if resolver is None:
        from utils.active_health_probe_core import _resolve_main_pid_status
        resolver = _resolve_main_pid_status
    try:
        status, _pid = resolver(unit)
    except Exception:
        return "unknown"
    return status if status in ("ok", "down", "absent") else "unknown"


def _unit_enabled(unit: str, timeout: float = 5.0) -> Optional[bool]:
    """Tri-state is-enabled: True / False / None (unobservable) — False only
    on a real "disabled"/"masked" answer, never on an error."""
    try:
        r = subprocess.run(["systemctl", "is-enabled", unit],
                           capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return None
    word = r.stdout.strip()
    if word in ("enabled", "enabled-runtime"):
        return True
    if word in ("disabled", "masked", "masked-runtime"):
        return False
    return None


def _organ_state(unit: str, resolver, enabled_fn) -> str:
    """``ok`` | ``down`` | ``down-disabled`` | ``absent`` | ``unknown``."""
    st = _unit_state(unit, resolver)
    if st == "down":
        try:
            en = (enabled_fn or _unit_enabled)(unit)
        except Exception:
            en = None
        return "down-disabled" if en is False else "down"
    return st


def _unreadable_leg(title: str, source: str, organ: str, why: str) -> Leg:
    """The map gave nothing: inert only when the daemon is absent by design."""
    if organ == "absent":
        return Leg(title, INERT, source,
                   why=f"no {DAEMON_UNIT} on this box — not a MeshAnchor gateway")
    if organ == "down-disabled":
        return Leg(title, INERT, source,
                   why=f"{DAEMON_UNIT} is installed but stopped AND disabled — "
                       f"a decision, not an outage")
    if organ == "down":
        return Leg(title, UNKNOWN, source,
                   why=f"{DAEMON_UNIT} is installed but NOT running (enabled, or "
                       f"its enablement could not be read); {why}")
    return Leg(title, UNKNOWN, source, why=why)


# ── legs ────────────────────────────────────────────────────────────────


def delivery_leg(now: float, organ: str, fetcher=None) -> Leg:
    title = "Gateway delivery record"
    source = MAP_BASE + "/api/gateway/delivery"
    snap, why = _fetch("/api/gateway/delivery", fetcher)
    if snap is None:
        return _unreadable_leg(title, source, organ, why)
    last = _num(snap.get("last_event_ts"))
    leg = Leg(title, OK, source, (now - last) if last is not None else None)
    health = snap.get("health") if isinstance(snap.get("health"), dict) else {}
    if health.get("db_unobservable") or health.get("preflight_ok") is False:
        leg.status = UNKNOWN
        leg.why = ("the daemon could not read its own delivery DB — "
                   f"{health.get('preflight_error') or 'snapshot read failed'}")
        return leg

    win = confirmation_window(snap)
    if not win["confirmable"]:
        leg.thin = True
        leg.lines.append("Recent window: no protocol has ever confirmed here — "
                         "cannot judge a rate (MeshCore and Meshtastic sends have "
                         "no end-to-end ACK here). Nothing proves an arrival.")
    elif win["ring"] is None:
        leg.thin = True
        leg.lines.append("Recent window: UNKNOWN — the events ring is missing; "
                         "the rate cannot be judged.")
    else:
        protos = ", ".join(sorted(win["confirmable"]))
        t = win["confirmed"] + win["failed"]
        if t < DELIVERY_STALL_MIN_TERMINAL:
            leg.thin = True
            leg.lines.append(
                f"Recent window ({protos}): {win['confirmed']} confirmed / "
                f"{win['failed']} failed — TOO FEW to judge ({t} of "
                f"{DELIVERY_STALL_MIN_TERMINAL} needed). Quiet, not proven healthy.")
        else:
            rate = win["confirmed"] / t
            leg.lines.append(
                f"Recent window ({protos}): {win['confirmed']} confirmed / "
                f"{win['failed']} failed of {t} -> {rate:.1%}")
            if rate <= _RATE_DEGRADED:
                leg.failing = True
                leg.lines.append(f"  !! at or below {_RATE_DEGRADED:.0%} — the "
                                 f"stall check calls this degraded")
        ts = [_num(e.get("ts")) for e in win["ring"] if isinstance(e, dict)]
        ts = [x for x in ts if x is not None]
        if ts:
            leg.lines.append(f"  window: {len(ts)} events over "
                             f"{(max(ts) - min(ts)) / 3600:.1f} h, newest "
                             f"{fmt_age(now - max(ts))} ago")

    tot = snap.get("state_totals") if isinstance(snap.get("state_totals"), dict) else {}
    drops = snap.get("drop_reasons") if isinstance(snap.get("drop_reasons"), dict) else {}
    nz = ", ".join(f"{k} {v}" for k, v in sorted(
        drops.items(), key=lambda kv: -(_num(kv[1]) or 0)) if (_num(v) or 0) > 0)
    first = _num(snap.get("first_event_ts"))
    since = time.strftime(" since %Y-%m-%d", time.localtime(first)) if first else ""
    leg.lines.append(f"Lifetime{since}: confirmed {tot.get('confirmed', '?')} · "
                     f"sent {tot.get('sent', '?')} · dropped {tot.get('dropped', '?')}")
    if nz:
        leg.lines.append(f"  drops: {nz}")
    uncon = _num(snap.get("unconfirmable_sent"))
    if uncon:
        leg.lines.append(f"  {int(uncon)} sent on protocols with no ACK — handed "
                         f"to the radio, arrival not provable")
    if last is not None:
        leg.lines.append(f"Last gateway event (any protocol): {fmt_age(now - last)} ago")
    errs = _num(health.get("consecutive_write_errors"))
    if errs:
        leg.failing = True
        leg.lines.append(f"  !! {int(errs)} consecutive write errors to the "
                         f"delivery DB: {health.get('last_write_error')}")
    return leg


def queue_leg(now: float, organ: str, fetcher=None) -> Leg:
    title = "Gateway queue"
    source = MAP_BASE + "/api/gateway/queue"
    stats, why = _fetch("/api/gateway/queue", fetcher)
    if stats is None:
        return _unreadable_leg(title, source, organ, why)
    ts = _num(stats.get("timestamp"))
    leg = Leg(title, OK, source, (now - ts) if ts is not None else None)
    g = stats.get
    # Two clocks in one record (MF 18f8a466): rows held in the queue DB vs
    # in-memory counters since the daemon started.
    leg.lines.append(
        f"held in the queue DB: delivered {g('delivered', '?')} · pending "
        f"{g('pending', '?')} · in progress {g('in_progress', '?')} · DEAD "
        f"LETTERS {g('dead_letter', '?')} · depth {g('queue_depth', '?')}/"
        f"{g('max_queue_size', '?')}")
    leg.lines.append(f"since the daemon started: failed {g('failed', '?')} · "
                     f"retried {g('retried', '?')} · shed {g('shed', '?')}")
    if (_num(g("dead_letter")) or 0) > 0 or (_num(g("failed")) or 0) > 0:
        leg.failing = True
    return leg


# ── entry point ─────────────────────────────────────────────────────────


def gather(home: Optional[str] = None, now: Optional[float] = None,
           unit_resolver=None, unit_enabled_fn=None, fetcher=None) -> DeliveryView:
    """Every leg for THIS box. The keyword arguments are test seams."""
    now = time.time() if now is None else now
    if home is None:
        from utils.paths import get_real_user_home
        home = str(get_real_user_home())
    organ = _organ_state(DAEMON_UNIT, unit_resolver, unit_enabled_fn)
    legs = [delivery_leg(now, organ, fetcher), queue_leg(now, organ, fetcher)]
    peers, leftovers = [], []
    for app, rel, unit in PEER_RECORDS:
        path = os.path.join(home, rel)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        st = _organ_state(unit, unit_resolver, unit_enabled_fn)
        if st in ("absent", "down-disabled"):
            leftovers.append((app, path, mtime))
        else:
            peers.append((app, path))
    return DeliveryView(host=socket.gethostname(), now=now, legs=legs,
                        peer_records=peers, peer_leftovers=leftovers)


def render(view: DeliveryView) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(view.now))
    out = [f"DELIVERY — {view.host}, read {stamp}", view.headline(), ""]
    label = {OK: "", INERT: "[inert] ", UNKNOWN: "[UNKNOWN] "}
    for leg in view.legs:
        age = f" (newest {fmt_age(leg.age_s)} old)" if leg.age_s is not None else ""
        flag = "[FAILING] " if leg.failing and leg.status == OK else ""
        out.append(f"── {label[leg.status]}{flag}{leg.title}{age}")
        if leg.status == OK:
            out.extend("  " + ln for ln in leg.lines)
        else:
            out.append("  " + leg.why)
        if leg.source:
            out.append(f"  source: {leg.source}")
        out.append("")
    for app, path in view.peer_records:
        out += [f"── [not read] {app} delivery record", f"  source: {path}", ""]
    for app, path, mtime in view.peer_leftovers:
        when = time.strftime("%Y-%m-%d", time.localtime(mtime))
        out += [f"── [leftover] {app} delivery file, last written {when}",
                f"  its writer unit is absent, disabled or masked here — a "
                f"leftover file, not a delivery organ", f"  source: {path}", ""]
    out += ["Read live from the local map (:5000), never from the delivery DB.",
            "MeshAnchor runs no soak exercisers, so no soak legs are shown.",
            "This screen never changes anything."]
    return "\n".join(out)


if __name__ == "__main__":  # pragma: no cover
    print(render(gather()))
