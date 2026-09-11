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
import time
from typing import Optional

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


def check_delivery_confirmation_stall(
    *,
    min_terminal: int = 20,
    rate_degraded: float = 0.50,
    rate_wedge: float = 0.10,
    snap: Optional[dict] = None,
) -> HealthResult:
    """A confirmable protocol's deliveries are failing instead of
    confirming (MF Issue #74 probe port; disjoint-protocol fix 2026-06-09).

    Windowed rate from the ``delivery_counters.snapshot()`` recent-events
    ring (``SNAPSHOT_RECENT_LIMIT`` events; 200 since 2026-08-10 — at 50
    a mesh-heavy gateway's ring held fewer confirmable terminals than
    ``min_terminal`` and this check sat permanently in ``low_traffic``).
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

    # Confirmable = protocols that have ever recorded a `confirmed` event.
    confirmed_by_proto = (snap.get("state_by_protocol") or {}).get("confirmed") or {}
    confirmable = {
        p for p, c in confirmed_by_proto.items()
        if isinstance(c, (int, float)) and not isinstance(c, bool) and c > 0
    }
    if not confirmable:
        return HealthResult(healthy=True, reason="no_confirmable_protocol")

    # Prefer the terminal-only ring: a general FIFO lets unconfirmable
    # traffic evict the terminals this check needs, so it reads
    # `low_traffic` while a TOTAL collapse reads the same. Ring size does
    # not fix that; density does. See TestConfirmationRingStarvation.
    recent = snap.get("recent_terminal")
    ring_source = "recent_terminal" if isinstance(recent, list) else "recent"
    if not isinstance(recent, list):
        recent = snap.get("recent")
    if not isinstance(recent, list):
        return HealthResult(healthy=True, reason="no_recent_ring")

    # Vocabulary owned by delivery_counters — imported, never re-listed
    # (honest_failure_modes #5: two independent hardcodes WILL drift).
    from gateway.delivery_counters import DELIVERY_FAILURE_REASONS
    ring_conf = 0
    ring_failed = 0
    for e in recent:
        if not isinstance(e, dict) or e.get("protocol") not in confirmable:
            continue
        st = e.get("state")
        if st == "confirmed":
            ring_conf += 1
        elif st == "dropped" and e.get("drop_reason") in DELIVERY_FAILURE_REASONS:
            ring_failed += 1

    terminal = ring_conf + ring_failed
    if terminal < min_terminal:
        # ⚠️ healthy=True here means "cannot judge", NOT "confirmations
        # are fine" — HealthResult is binary. Read the reason, not the bool.
        stale = "" if ring_source == "recent_terminal" else " (legacy `recent` ring)"
        return HealthResult(
            healthy=True,
            reason=(f"low_traffic terminal={terminal}<{min_terminal} "
                    f"in {len(recent)} `{ring_source}` events{stale}"),
        )

    rate = ring_conf / terminal
    if rate > rate_degraded:
        return HealthResult(
            healthy=True,
            reason=f"confirm_ok {ring_conf}/{terminal} ({rate:.0%})",
        )

    level = "wedge" if rate <= rate_wedge else "degraded"
    protos = ", ".join(sorted(confirmable))
    return HealthResult(
        healthy=False,
        reason=(
            f"delivery_confirmation_stall ({level}): {ring_conf}/{terminal} "
            f"{protos} messages confirmed in the recent window ({rate:.0%}); "
            f"the rest failed delivery. Check RNS paths to the fan-out peers "
            f"and /api/gateway/delivery drop_reasons."
        ),
    )
