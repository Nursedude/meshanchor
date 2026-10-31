"""Companion-radio clock: MEASURE always, WRITE only on opt-in.

Born 2026-10-31. meshanchor-server's RAK1 ran 8.29 days slow — measured as
the median of (sender ``last_advert`` - our ``lastmod``) over 98 contacts
with sane sender clocks, fresh adverts reading sender-age 0.00 d. The app
never read the radio's clock, so nothing said so; the contacts pane had
recorded the symptom on 09-21 and blamed firmware semantics. The radio
stamps every contact receipt AND its own outbound adverts/messages with
this RTC, so its offset is a fact the app must SAY.

Contract:
  * ``measure`` reads ``get_time`` and never writes.
  * ``sync_if_allowed`` writes ``set_time`` ONLY when the operator opted in
    (``gateway.json[meshcore].sync_clock_on_connect``), the host clock is
    NTP-synchronised (an RTC-less Pi on fake-hwclock would write a stale
    time — persistent_issues "clock ran days-stale"), and the radio is
    measurably skewed. Success is judged from a RE-READ, never assumed.
  * An unsynced host never yields ``ok`` or ``skewed`` — it cannot judge.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

#: Beyond this |offset| the radio clock is called skewed. Two minutes covers
#: serial latency and second truncation with a wide margin; the defect this
#: exists for was 8 days.
SKEW_TOLERANCE_S = 120.0

_TIMEDATECTL = ["timedatectl", "show", "-p", "NTPSynchronized", "--value"]


def host_clock_synced() -> Optional[bool]:
    """True / False from systemd, None when it cannot be asked."""
    try:
        cp = subprocess.run(_TIMEDATECTL, capture_output=True, text=True,
                            timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    val = (cp.stdout or "").strip().lower()
    if val == "yes":
        return True
    if val == "no":
        return False
    return None


def judge(device_epoch: Any, host_epoch: float, *,
          host_synced: Optional[bool]) -> Dict[str, Any]:
    """Pure verdict. ``offset_s`` = device - host (negative = radio slow)."""
    try:
        dev = float(device_epoch) if device_epoch else None
    except (TypeError, ValueError):
        dev = None
    out: Dict[str, Any] = {
        "device_epoch": dev, "host_epoch": host_epoch,
        "host_synced": host_synced, "offset_s": None,
        "state": "unobservable", "reason": None,
        "tolerance_s": SKEW_TOLERANCE_S,
    }
    if dev is None:
        out["reason"] = "radio reported no time"
        return out
    out["offset_s"] = dev - host_epoch
    if host_synced is not True:
        out["state"] = "host_unsynced"
        out["reason"] = ("host clock not NTP-synchronised" if host_synced is False
                         else "host NTP state unknown")
        return out
    out["state"] = "ok" if abs(out["offset_s"]) <= SKEW_TOLERANCE_S else "skewed"
    return out


async def measure(commands: Any, event_type: Any, *,
                  now: Callable[[], float] = time.time,
                  host_synced_fn: Callable[[], Optional[bool]] = host_clock_synced,
                  ) -> Dict[str, Any]:
    """Read the radio clock. Never raises, never writes."""
    try:
        evt = await commands.get_time()
    except Exception as e:  # serial gone / timeout — unobservable, say why
        v = judge(None, now(), host_synced=host_synced_fn())
        v["reason"] = f"get_time failed: {e}"
        return v
    if getattr(evt, "type", None) != event_type.CURRENT_TIME:
        v = judge(None, now(), host_synced=host_synced_fn())
        v["reason"] = f"get_time returned {getattr(evt, 'type', None)}"
        return v
    payload = getattr(evt, "payload", None) or {}
    return judge(payload.get("time"), now(), host_synced=host_synced_fn())


async def sync_if_allowed(commands: Any, event_type: Any, *, allow: bool,
                          now: Callable[[], float] = time.time,
                          host_synced_fn: Callable[[], Optional[bool]] = host_clock_synced,
                          ) -> Dict[str, Any]:
    """Measure; write ``set_time`` only under the module contract."""
    before = await measure(commands, event_type, now=now,
                           host_synced_fn=host_synced_fn)
    if before["state"] == "unobservable":
        before["sync"] = "not_attempted_unobservable"
        return before
    if before["state"] == "host_unsynced":
        before["sync"] = "refused_host_unsynced" if allow else "not_opted_in"
        return before
    if before["state"] == "ok":
        before["sync"] = "not_needed"
        return before
    if not allow:
        before["sync"] = "not_opted_in"
        return before

    target = int(now())
    try:
        res = await commands.set_time(target)
        acked = getattr(res, "type", None) == event_type.OK
    except Exception as e:
        logger.warning(f"MeshCore clock sync: set_time failed: {e}")
        acked = False
    after = await measure(commands, event_type, now=now,
                          host_synced_fn=host_synced_fn)
    after["offset_before_s"] = before["offset_s"]
    after["sync"] = "synced" if (acked and after["state"] == "ok") else "failed"
    logger.warning(
        "MeshCore clock sync: offset %.0fs -> %s (sync=%s, acked=%s)",
        before["offset_s"],
        "?" if after["offset_s"] is None else f"{after['offset_s']:.0f}s",
        after["sync"], acked,
    )
    return after


def describe(clock: Optional[Dict[str, Any]]) -> str:
    """One operator line for the TUI/logs. Never empty."""
    if not clock:
        return "radio clock: not measured"
    st = clock.get("state")
    off = clock.get("offset_s")
    if st == "unobservable" or off is None:
        return f"radio clock: UNKNOWN ({clock.get('reason') or 'no reading'})"
    mag = abs(off)
    human = (f"{mag / 86400:.1f} d" if mag >= 86400 else
             f"{mag / 3600:.1f} h" if mag >= 3600 else f"{mag:.0f} s")
    way = "slow" if off < 0 else "fast"
    if st == "ok":
        return f"radio clock: ok ({human} {way})"
    if st == "host_unsynced":
        return (f"radio clock: {human} {way} vs host — host NOT NTP-synced, "
                "cannot judge")
    return (f"radio clock: SKEWED {human} {way} — its adverts, messages and "
            "contact times carry this clock")
