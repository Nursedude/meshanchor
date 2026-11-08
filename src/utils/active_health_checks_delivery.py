"""Delivery-path health checks — queue backlog and confirmation stall.

Split out of ``active_health_probe.py`` on 2026-09-10: that file sat at
EXACTLY the 1,500-line MF025 cap, so the next change to it could not land.
The 2026-08-12 split moved the *primitives* into
``active_health_probe_core``; this one moves the *checks*, which are the
bulk — 9 of 11 never touched ``self`` at all.

Mirrors MeshForge, the lead repo for this arc, where the same checks live in
per-domain ``watchdog_probes_*.py`` modules beside a shared
``watchdog_probe_core``. These are free functions: ``ActiveHealthProbe``
re-exposes each one so ``probe.check_x(...)`` and every registered lambda
keep working unchanged.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any, List, Optional

from utils.active_health_probe_core import HealthResult

logger = logging.getLogger(__name__)



def check_queue_backlog(
    dl_samples,
    *,
    depth_degraded: float = 0.80,
    depth_wedge: float = 0.95,
    dl_growth_degraded: int = 10,
    dl_growth_wedge: int = 50,
    growth_window_s: float = 300.0,
    now: Optional[float] = None,
    stats: Optional[dict] = None,
) -> HealthResult:
    """Persistent-queue backpressure (MF Issue #74 probe port).

    A deep backlog masks delivery failures: messages sit 'pending'
    while the operator reads the gateway as healthy, and at the
    shed threshold ``_shed_overflow`` silently drops LOW/NORMAL
    priority. Two legs:

    - depth: queue_depth / max_queue_size ≥ 95% flags ``wedge``
      (shed imminent/active), ≥ 80% ``degraded``. Skipped when
      max_queue_size ≤ 0 (unlimited — no ceiling to judge, mirrors
      the fd check's "unlimited" guard).
    - dead-letter GROWTH over a trailing ``growth_window_s`` deque
      (instance state — the MA-native replacement for MF's
      persisted-baseline file): a one-tick spike stays ≥ threshold
      vs the oldest in-window sample for the full window (~10
      ticks at 30s), latching past the fails=3 hysteresis, then
      self-heals as the elevated count becomes the new baseline.
      A static historical pile never fires.

    Reads ``PersistentMessageQueue().get_stats()`` IN-PROCESS — the
    probe runs as the operator inside the daemon/agent (unlike
    MF's sandboxed root watchdog, which must go over localhost
    HTTP). Healthy (quiet) when stats are unavailable — a box with
    no gateway/queue must not false-alarm.

    ``now``/``stats`` are test seams (fd check's injection style).
    """
    if stats is None:
        try:
            from gateway.message_queue import PersistentMessageQueue
            stats = PersistentMessageQueue().get_stats()
        except Exception:
            return HealthResult(healthy=True, reason="queue_stats_unavailable")
    try:
        max_q = int(stats.get("max_queue_size") or 0)
        depth = int(stats.get("queue_depth") or 0)
        dead = int(stats.get("dead_letter") or 0)
    except (TypeError, ValueError):
        return HealthResult(healthy=True, reason="queue_stats_malformed")

    ts_now = now if now is not None else time.time()
    findings = []  # (level, fragment)

    if max_q > 0:
        usage = depth / max_q
        if usage >= depth_wedge:
            findings.append((
                "wedge",
                f"queue at {usage:.0%} of max ({depth}/{max_q}) — "
                f"shed threshold; LOW/NORMAL priority messages are "
                f"being dropped",
            ))
        elif usage >= depth_degraded:
            findings.append((
                "degraded",
                f"queue backlog building: {usage:.0%} of max "
                f"({depth}/{max_q})",
            ))

    dl_samples.append((ts_now, dead))
    while dl_samples and ts_now - dl_samples[0][0] > growth_window_s:
        dl_samples.popleft()
    baseline = dl_samples[0][1]
    growth = dead - baseline
    if growth >= dl_growth_wedge:
        findings.append((
            "wedge",
            f"dead-letter +{growth} in {growth_window_s / 60:.0f}m "
            f"(now {dead}) — retries exhausting en masse",
        ))
    elif growth >= dl_growth_degraded:
        findings.append((
            "degraded",
            f"dead-letter +{growth} in window (now {dead})",
        ))

    if not findings:
        return HealthResult(
            healthy=True,
            reason=f"queue_ok depth={depth}/{max_q} dl={dead}",
        )

    level = "wedge" if any(lv == "wedge" for lv, _ in findings) else "degraded"
    return HealthResult(
        healthy=False,
        reason=(
            f"queue_backlog ({level}): "
            + "; ".join(f for _, f in findings)
            + ". Check /api/gateway/queue and the gateway journal "
            "for the failing destination."
        ),
    )


#: Confirmable terminal events needed before a rate is judged — named so the
#: TUI Delivery screen says "too few to judge" at the SAME floor.
DELIVERY_STALL_MIN_TERMINAL = 20


#: The rate verdict's window (2026-11-07, port of MF e31fe908): confirmable
#: terminals newer than this count — a quiet gateway gets a slow verdict
#: instead of none; a busy one still clears the floor in minutes.
DELIVERY_STALL_WINDOW_S = 7 * 86400.0

#: The collapse COUNTER (observation only by operator decision 2026-11-07 —
#: reported in the reason text, NEVER flips `healthy`, until a soak baseline
#: says what this box produces). The dated judged rows stay in their pool, so
#: that baseline is re-derivable from the DB itself.
COLLAPSE_COUNTER_WINDOW_S = 72 * 3600.0
COLLAPSE_COUNTER_MIN_TERMINAL = 5

#: Rows ``snapshot()`` serves as ``recent_terminal``
#: (gateway.delivery_counters.SNAPSHOT_RECENT_LIMIT, test-pinned). A full ring
#: with nothing aged out is COUNT-bounded — said so, never "7 d".
SERVED_TERMINAL_LIMIT = 200

#: An event stamped further ahead than this is a clock artifact: kept in the
#: window (evidence) but of unknown age, so never counted as recent (hfm #6).
FUTURE_SLOP_S = 900.0


def event_ts(e: dict, now: Optional[float] = None) -> Optional[float]:
    """The ONE reading of an event's age stamp, for the window AND the TUI
    span line (2026-11-07: the span line parsed it a second way and printed
    "over 500000.0 h" beside "1 of unknown age"). None = unknown age: not a
    finite positive number, or — given ``now`` — stamped beyond FUTURE_SLOP_S
    ahead (a clock artifact)."""
    v = _ts(e)
    if v is not None and now is not None and v > now + FUTURE_SLOP_S:
        return None
    return v


def _ts(e: dict) -> Optional[float]:
    v = e.get("ts")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    # ts <= 0 is an absent-value sentinel, not a 1970 event (the 56.7-year
    # `now - 0` class, hfm #6): unknown age, never "older outside".
    return v if math.isfinite(v) and v > 0 else None


def confirmation_window(snap: dict, *, now: Optional[float] = None,
                        window_s: float = DELIVERY_STALL_WINDOW_S) -> dict:
    """The windowed confirmation count ``check_delivery_confirmation_stall``
    judges, as a pure function of a delivery snapshot — one implementation
    for the check and the TUI Delivery screen (hfm #5, 2026-09-23).

    Keys: ``confirmable`` (set of protocols that have ever confirmed),
    ``ring_source`` (``recent_terminal`` | ``recent``), ``ring`` (the
    in-window entries of the ring judged, or None when neither ring is a
    list), ``confirmed``, ``failed``, and since 2026-11-07 ``ring_total``,
    ``window_s``, ``out_of_window``, ``undated``, ``truncated``, ``span_s``,
    ``collapse`` (the 72 h observation counter).

    Set membership and "walk the ring only when something can confirm" are
    kept from the 2026-09-23 extraction (a MeshForge twin swapped the set for
    a list and changed behaviour on corrupt payloads — its review caught it).

    Wall clock is forgeable (hfm #6): an event stamped beyond FUTURE_SLOP_S
    ahead, or undated, stays in the window but never counts as recent; a
    clock that jumped FORWARD ages everything out → too few, never healthy.

    Prefer the terminal-only ring: a general FIFO lets unconfirmable traffic
    evict the terminals this needs, so it reads `low_traffic` while a TOTAL
    collapse reads the same. See TestConfirmationRingStarvation.
    """
    now = time.time() if now is None else now
    confirmed_by_proto = (snap.get("state_by_protocol") or {}).get("confirmed") or {}
    confirmable = {
        p for p, c in confirmed_by_proto.items()
        if isinstance(c, (int, float)) and not isinstance(c, bool) and c > 0
    }
    recent = snap.get("recent_terminal")
    ring_source = "recent_terminal" if isinstance(recent, list) else "recent"
    if not isinstance(recent, list):
        recent = snap.get("recent")
    if not isinstance(recent, list):
        recent = None
    # Vocabulary owned by delivery_counters — imported, never re-listed.
    from gateway.delivery_counters import is_judged_terminal
    cutoff = now - window_s
    short_cutoff = now - COLLAPSE_COUNTER_WINDOW_S
    conf = failed = out_of_window = undated = 0
    s_conf = s_failed = 0
    oldest = newest = None
    reaches_past_window = False   # ANY dated entry older than the cutoff
    in_window: List[Any] = []
    # Walk the ring only when something can confirm — the inline code this
    # replaced returned `no_confirmable_protocol` BEFORE touching the ring,
    # so a corrupt entry (unhashable protocol) never raised on a box with no
    # confirmable protocol (review B differential fuzz, 2026-09-23).
    for e in (recent or ()) if confirmable else ():
        if not isinstance(e, dict):
            continue
        judged = (e.get("protocol") in confirmable
                  and is_judged_terminal(e.get("state"), e.get("drop_reason")))
        ts = event_ts(e, now)   # beyond slop → None: keep the evidence, not its age
        if ts is not None and ts < cutoff:
            out_of_window += judged
            reaches_past_window = True
            continue
        in_window.append(e)
        if not judged:
            continue
        is_conf = e.get("state") == "confirmed"
        conf += is_conf
        failed += not is_conf
        if ts is None:
            undated += 1
            continue
        oldest = ts if oldest is None else min(oldest, ts)
        newest = ts if newest is None else max(newest, ts)
        if ts >= short_cutoff:
            s_conf += is_conf
            s_failed += not is_conf
    s_terminal = s_conf + s_failed
    ring_total = sum(1 for e in recent or () if isinstance(e, dict))
    return {
        "confirmable": confirmable, "ring_source": ring_source,
        "ring": (in_window if confirmable else recent) if recent is not None else None,
        "ring_total": ring_total,
        "confirmed": conf, "failed": failed, "window_s": window_s,
        "out_of_window": out_of_window, "undated": undated,
        # Count-bounded, not age-bounded: the served ring is full and NO entry
        # (of any protocol — Fable MA r1 L2) reaches past the window edge, so
        # the oldest judged event is a ring edge (either ring).
        "truncated": ring_total >= SERVED_TERMINAL_LIMIT and not reaches_past_window,
        "span_s": (newest - oldest) if (oldest is not None and newest is not None) else None,
        "collapse": {
            "window_s": COLLAPSE_COUNTER_WINDOW_S,
            "terminal": s_terminal, "confirmed": s_conf, "failed": s_failed,
            "min_terminal": COLLAPSE_COUNTER_MIN_TERMINAL,
            "candidate": (s_terminal >= COLLAPSE_COUNTER_MIN_TERMINAL and s_conf == 0),
            "observe_only": True,
        },
    }


def _dur(seconds: float) -> str:
    """168 h → "7 d", 72 h → "72 h", 47.8 h → "47.8 h"."""
    hrs = seconds / 3600.0
    if hrs > 72:
        d = hrs / 24
        return f"{d:g} d" if d == int(d) else f"{d:.1f} d"
    return f"{hrs:g} h" if hrs == int(hrs) else f"{hrs:.1f} h"


def format_window(win: dict) -> str:
    """One short phrase naming the window and the collapse counter, for the
    check's reason and the TUI (one wording, two surfaces); labels derive
    from the constants the counts used."""
    c = win.get("collapse") or {}
    if win.get("truncated"):
        span = win.get("span_s")
        noun = ("terminals" if win.get("ring_source") == "recent_terminal"
                else f"`{win.get('ring_source')}` events")
        parts = [f"newest {win.get('ring_total')} {noun}"
                 + (f", spanning {_dur(span)}" if span is not None else "")
                 + f" (count-bounded, under {_dur(win.get('window_s', DELIVERY_STALL_WINDOW_S))})"]
    else:
        parts = [f"last {_dur(win.get('window_s', DELIVERY_STALL_WINDOW_S))}"]
    if win.get("out_of_window"):
        parts.append(f"{win['out_of_window']} older outside")
    if win.get("undated"):
        parts.append(f"{win['undated']} of unknown age")
    parts.append(f"{_dur(c.get('window_s', COLLAPSE_COUNTER_WINDOW_S))}: "
                 f"{c.get('terminal', 0)} terminal / {c.get('confirmed', 0)} confirmed"
                 + (" — COLLAPSE CANDIDATE (observe-only)" if c.get("candidate") else ""))
    return "; ".join(parts)


def check_delivery_confirmation_stall(
    *,
    min_terminal: int = DELIVERY_STALL_MIN_TERMINAL,
    rate_degraded: float = 0.50,
    rate_wedge: float = 0.10,
    snap: Optional[dict] = None,
    now: Optional[float] = None,
) -> HealthResult:
    """A confirmable protocol's deliveries are failing instead of
    confirming (MF Issue #74 probe port; disjoint-protocol fix 2026-06-09).

    Windowed rate over the judged terminals of the last 7 days
    (``DELIVERY_STALL_WINDOW_S``, 2026-11-07) from the
    ``delivery_counters.snapshot()`` ``recent_terminal`` ring (newest
    ``SNAPSHOT_RECENT_LIMIT`` judged rows; a full ring is labelled
    count-bounded). Before 2026-11-07 mesh `sent` rows evicted the RNS
    terminals from the shared pool and this sat in ``low_traffic`` on
    meshanchor-server (8 terminals over 31.7 h).
    CRUCIAL: judges ONLY protocols that actually have a
    confirmation mechanism (record `confirmed` events — RNS today;
    Meshtastic once ACK consumption lands), comparing that protocol's two
    REAL terminal outcomes — `confirmed` vs a failed-delivery `dropped` —
    NOT the meaningless cross-population `confirmed/sent` ratio. The
    counters use disjoint lifecycle states per protocol (RNS:
    queued→confirmed, never `sent`; Meshtastic: queued→sent, never
    `confirmed`), so `confirmed/sent` was (RNS-confirmed ÷ mesh-sent) —
    two different populations that never measured a coherent rate and
    false-alarmed ~50% on every mesh-heavy gateway.

    Self-guards healthy (silence is NOT failure here): counters
    unavailable; no confirmable protocol (nothing tracks confirmation);
    confirmable terminal events < ``min_terminal`` (one failure must not
    tank a tiny denominator — honest over too small a sample; the ring
    is sized so a busy gateway clears this floor, see the
    cross-constant test). ``snap`` is a test seam.
    """
    if snap is None:
        try:
            from gateway.delivery_counters import snapshot as _snapshot
            snap = _snapshot()
        except Exception:
            return HealthResult(
                healthy=True, reason="delivery_counters_unavailable",
            )
    if not isinstance(snap, dict):
        return HealthResult(healthy=True, reason="no_traffic")

    # The count lives in confirmation_window() — shared with the TUI Delivery
    # screen so the operator reads the SAME judgement (hfm #5, 2026-09-23).
    win = confirmation_window(snap, now=now)
    confirmable = win["confirmable"]
    if not confirmable:
        return HealthResult(healthy=True, reason="no_confirmable_protocol")
    recent, ring_source = win["ring"], win["ring_source"]
    if recent is None:
        return HealthResult(healthy=True, reason="no_recent_ring")
    ring_conf, ring_failed = win["confirmed"], win["failed"]
    terminal = ring_conf + ring_failed
    if terminal < min_terminal:
        # ⚠️ healthy=True here means "cannot judge", NOT "confirmations
        # are fine" — HealthResult is binary. Read the reason, not the bool.
        stale = "" if ring_source == "recent_terminal" else " (legacy `recent` ring)"
        return HealthResult(
            healthy=True,
            reason=(f"low_traffic terminal={terminal}<{min_terminal} "
                    f"in {len(recent)} in-window of {win['ring_total']} "
                    f"`{ring_source}` events; {format_window(win)}{stale}"),
        )

    rate = ring_conf / terminal
    if rate > rate_degraded:
        return HealthResult(
            healthy=True,
            reason=f"confirm_ok {ring_conf}/{terminal} ({rate:.0%}); {format_window(win)}",
        )

    level = "wedge" if rate <= rate_wedge else "degraded"
    protos = ", ".join(sorted(confirmable))
    return HealthResult(
        healthy=False,
        reason=(
            f"delivery_confirmation_stall ({level}): {ring_conf}/{terminal} "
            f"{protos} messages confirmed in the recent window ({rate:.0%}); "
            f"the rest failed delivery. Check RNS paths to the fan-out peers "
            f"and /api/gateway/delivery drop_reasons. [{format_window(win)}]"
        ),
    )
