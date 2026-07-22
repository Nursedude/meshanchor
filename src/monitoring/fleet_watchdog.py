"""Fleet silence watchdog — turns silence into a visible alert.

S5a of the fleet observability charter. Runs as a separate process
from the collector so a collector crash doesn't silence detection.
Periodically reads the most recent heartbeat row(s) and classifies:

- **HTTP-dead** — most recent heartbeat older than ``stale_threshold_s``.
  The collector can't reach the map, or the map service is down.
- **Frozen** — heartbeats are landing but ``uptime_s`` isn't strictly
  increasing across the last 3 cycles. The map answers `200 OK` but the
  ``uptime_s`` source is stuck (note: this is the MAP's uptime today —
  see daemon_dead below for daemon-specific detection).
- **No data** — heartbeat table is empty. Collector never ran or the
  history DB was just created. Emits an early-warning blackout so the
  operator sees "the platform isn't running" rather than nothing.
- **Daemon-dead** — ``meshanchor-daemon.service`` is not active for
  ≥ ``HYSTERESIS_CYCLES`` consecutive checks. Uses ``check_service``
  directly — independent of heartbeat freshness, so it catches the
  exact failure mode the post-S5b BLACKOUT smoke surfaced (2026-05-09):
  daemon down + map up means heartbeats land fine and frozen-rule
  reads the map's still-incrementing uptime, but the back end is
  silent.

- **Mini-dead** — the operator's ``mini_dudeai`` (the second brain, WS-A)
  is present but its ``mini_dudeai_state.json`` stopped advancing for
  ≥ ``MINI_DEAD_STALE_S``. The EXTERNAL watcher for total mini death, which
  the mini's own within-tick MiniSelfSource structurally cannot see (a source
  can't run if its loop is dead). Inert on a box with no mini installed;
  silent on a graceful stop (clean-exit marker).

These flavors of silence are non-negotiable: a healthy HTTP layer over
a dead daemon is the failure mode that's hardest to catch by accident,
and the operator's "things don't fall silent" requirement is what
this whole charter is about.

Lifecycle
---------
- Each detected condition opens an active blackout row of the matching
  ``kind``. ``record_blackout_started`` is idempotent per kind so
  consecutive cycles don't accumulate duplicate rows.
- When the condition clears, we close the row by kind.
- Multiple kinds can be active at once (e.g. http_dead + frozen if the
  watchdog's reading the heartbeat from a stale snapshot during a flap).

Intended deployment (S5b adds the unit):

    systemd unit calls:  python3 -m monitoring.fleet_watchdog

Manual smoke:

    python3 -m monitoring.fleet_watchdog --interval 30 --stale 120
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("fleet.watchdog")


DEFAULT_INTERVAL_S = 30.0
"""How often the watchdog runs a check. Half the collector's cadence
keeps detection latency tight without burning cycles."""

DEFAULT_STALE_THRESHOLD_S = 120.0
"""Heartbeat older than this ⇒ HTTP-dead. 2× the collector's 60s
interval gives one full cycle of grace before alerting."""

DEFAULT_FROZEN_WINDOW_CYCLES = 3
"""How many recent heartbeats to require for the frozen check.
Three points means we need ≥2 minutes of "no advancement" before
flagging — long enough to ride out a single SQLite write contention,
short enough to catch a real wedge inside the operator's attention
window."""

DAEMON_HYSTERESIS_CYCLES = 2
"""How many consecutive ``not active`` reads of meshanchor-daemon
trigger the daemon_dead blackout. Two cycles × 30s default interval
= 60s minimum to fire — long enough to ride out a daemon `restart`
mid-cycle (which typically completes in <5s), short enough to satisfy
the smoke procedure's ~120s daemon-stop window."""

# Blackout kind classifiers. Stored in `blackout_events.kind`.
KIND_NO_DATA = "no_data"
KIND_HTTP_DEAD = "http_dead"
KIND_FROZEN = "frozen"
KIND_DAEMON_DEAD = "daemon_dead"
KIND_ROLE_DRIFT = "role_drift"
KIND_MINI_DEAD = "mini_dead"
ALL_KINDS = (KIND_NO_DATA, KIND_HTTP_DEAD, KIND_FROZEN, KIND_DAEMON_DEAD,
             KIND_ROLE_DRIFT, KIND_MINI_DEAD)

MINI_DEAD_STALE_S = 300.0
"""mini_dudeai_state.json older than this ⇒ the second brain is dead/wedged.
5 min ≈ 10 missed 30s ticks — long enough to ride out a mini restart, short
enough to catch a real wedge. This is the EXTERNAL watcher for total mini
death, which the mini's own within-tick MiniSelfSource structurally cannot see
(WS-A: a source can't run if the loop that runs it is dead)."""

MINI_DEAD_HYSTERESIS_CYCLES = 2
"""Consecutive stale reads before mini_dead fires — same debounce shape as
DAEMON_HYSTERESIS_CYCLES; rides out a mini restart mid-cycle."""

ROLE_DRIFT_HYSTERESIS_CYCLES = 2
"""How many consecutive cycles of confirmed role drift open the
role_drift blackout. Role catalog (git) and unit state (converge /
restarts) deploy independently, so a single cycle can catch a deploy
window; two cycles = ~60s at the default interval before firing (same
rationale + shape as DAEMON_HYSTERESIS_CYCLES / MeshForge's debounce_ticks)."""


# Module-level hysteresis state for daemon_dead. The watchdog runs as
# a single long-lived process so this is safe; multiple concurrent
# watchdogs would corrupt the streak counter, but that's already
# disallowed by the systemd unit (single instance).
_daemon_state: Dict[str, int] = {"inactive_streak": 0}

# Same pattern for role_drift (independent counter).
_role_drift_state: Dict[str, int] = {"drift_streak": 0}

# Same pattern for mini_dead (independent counter).
_mini_dead_state: Dict[str, int] = {"stale_streak": 0}


def _reset_daemon_state() -> None:
    """Test helper — resets the daemon_dead streak counter to 0."""
    _daemon_state["inactive_streak"] = 0


def _reset_role_drift_state() -> None:
    """Test helper — resets the role_drift streak counter to 0."""
    _role_drift_state["drift_streak"] = 0


def _reset_mini_dead_state() -> None:
    """Test helper — resets the mini_dead streak counter to 0."""
    _mini_dead_state["stale_streak"] = 0


def _check_daemon_active() -> Optional[bool]:
    """Probe meshanchor-daemon.service via ``check_service``.

    Returns:
        True  — service is active.
        False — service is not active (any state ≠ available).
        None  — probe raised; treat as "no signal" so the streak
                doesn't advance OR reset on transient systemctl errors.
    """
    try:
        from utils.service_check import check_service
        status = check_service("meshanchor-daemon")
        return bool(getattr(status, "available", False))
    except Exception as e:
        logger.debug("check_service('meshanchor-daemon') raised: %s", e)
        return None


# ──────────────────────────────────────────────────────────────────────
# Detection
# ──────────────────────────────────────────────────────────────────────


def _daemon_dead_reason() -> Optional[str]:
    """Evaluate the daemon-dead signal (INDEPENDENT of heartbeat data) and
    advance the hysteresis streak. Returns a reason when active, else None.

    Extracted so it runs even when the heartbeat table is empty — otherwise the
    empty-table early return in detect_silence would abstain on daemon_dead, and
    reconcile_blackouts would CLOSE a genuinely-valid daemon_dead blackout (the
    daemon is actually down). Absence-of-evaluation ≠ recovery (honest_failure
    #2). (QA audit 2026-07-06.)"""
    daemon_active = _check_daemon_active()
    if daemon_active is True:
        _daemon_state["inactive_streak"] = 0
    elif daemon_active is False:
        _daemon_state["inactive_streak"] += 1
        if _daemon_state["inactive_streak"] >= DAEMON_HYSTERESIS_CYCLES:
            return (
                f"meshanchor-daemon.service not active for "
                f"{_daemon_state['inactive_streak']} consecutive checks"
            )
    # daemon_active is None → probe failed; leave streak unchanged.
    return None


def _role_drift_reason() -> Optional[str]:
    """Evaluate the role-drift signal (INDEPENDENT of heartbeat data) and advance
    the hysteresis streak. Returns a reason when confirmed over
    ``ROLE_DRIFT_HYSTERESIS_CYCLES`` consecutive cycles, else None.

    A drift verdict (reason string) advances the streak; a clean OR indeterminate
    verdict (evaluate_role_drift -> None: converged, no role, or tool/catalog
    unavailable) RESETS it — never accumulate toward alarm on an unobservable
    cycle (honest_failure #2). Like _daemon_dead_reason, this is evaluated even
    when the heartbeat table is empty, so a reset table can't wrongly close a
    genuinely-active role_drift blackout."""
    try:
        from utils.role_drift import evaluate_role_drift
        reason = evaluate_role_drift()
    except Exception as e:  # never let the role probe sink a watchdog cycle
        logger.debug("evaluate_role_drift raised: %s", e)
        return None
    if reason is None:
        _role_drift_state["drift_streak"] = 0
        return None
    _role_drift_state["drift_streak"] += 1
    if _role_drift_state["drift_streak"] >= ROLE_DRIFT_HYSTERESIS_CYCLES:
        return (
            f"{reason} | confirmed over "
            f"{_role_drift_state['drift_streak']} consecutive checks"
        )
    return None  # divergence seen, not yet confirmed across cycles


def _mini_dead_reason(now: float) -> Optional[str]:
    """Evaluate the mini_dead signal — is the operator's mini_dudeai (the
    second brain) present but no longer ticking? The mini's OWN within-tick
    MiniSelfSource cannot detect total death (a source can't run if its loop is
    dead), so this EXTERNAL watcher closes that gap (WS-A). Independent of
    heartbeat data (like daemon_dead / role_drift), so it is evaluated even when
    the heartbeat table is empty.

    Self-guards, in order (each an honest_failure_modes #2 boundary — a degraded
    or absent observation must NOT read as a positive 'dead' verdict):
      * state file ABSENT → the mini is not installed on this box → None
        (declared-absent ≠ dead), streak reset.
      * state unreadable / no ``last_tick_ts`` → indeterminate → None, streak
        left unchanged (a transient read error must neither accuse nor clear).
      * fresh (age ≤ threshold) → alive → None, streak reset.
      * stale BUT a clean-exit marker newer than the last tick → the operator
        stopped it gracefully (the engine stamps that marker on SIGTERM) → None,
        streak reset. Paging an intentional stop would be the false-alarm this
        guard exists to prevent.
    Only a present, past-threshold-stale state with no fresh clean-exit,
    confirmed over the hysteresis window, returns a reason.
    """
    import json
    import os
    try:
        from utils.paths import get_real_user_home
        home = str(get_real_user_home())
        # The MA mini's OWN namespaced dir — NOT $HOME/mini_dudeai_* (that is the
        # MeshForge mini's; on a dual-stack box both run, so mini_dead must watch
        # the MeshAnchor one). Kept in lockstep with meshanchor_fleet.ma_mini_dir
        # (honest_failure_modes #5: two consumers, one location — test-pinned).
        ma_dir = os.path.join(home, ".local", "share", "meshanchor", "mini")
        state_path = os.path.join(ma_dir, "state.json")
        clean_path = os.path.join(ma_dir, "clean_exit")
    except Exception as e:  # home resolution failed — can't observe, don't accuse
        logger.debug("mini_dead home resolution raised: %s", e)
        return None
    if not os.path.exists(state_path):
        _mini_dead_state["stale_streak"] = 0
        return None  # mini not installed here — not applicable
    try:
        with open(state_path, encoding="utf-8") as f:
            doc = json.load(f)
        last_tick = float(doc.get("last_tick_ts", 0.0) or 0.0)
    except (OSError, ValueError, TypeError) as e:
        logger.debug("mini_dead state read raised: %s", e)
        return None  # indeterminate — leave streak unchanged
    if last_tick <= 0.0:
        _mini_dead_state["stale_streak"] = 0
        return None  # never ticked / no timestamp — not a death signal
    age = max(0.0, now - last_tick)  # clamp a future ts (clock step), as http_dead does
    if age <= MINI_DEAD_STALE_S:
        _mini_dead_state["stale_streak"] = 0
        return None  # fresh — the loop is alive
    try:
        if os.path.exists(clean_path) and os.stat(clean_path).st_mtime >= last_tick:
            _mini_dead_state["stale_streak"] = 0
            return None  # graceful stop — not a death
    except OSError:
        pass  # marker unreadable → treat as no graceful stop, fall through
    _mini_dead_state["stale_streak"] += 1
    if _mini_dead_state["stale_streak"] >= MINI_DEAD_HYSTERESIS_CYCLES:
        return (
            f"mini_dudeai state is {age:.0f}s stale (threshold "
            f"{MINI_DEAD_STALE_S:.0f}s) with no fresh clean-exit — the second "
            f"brain is dead or wedged"
        )
    return None  # stale seen, not yet confirmed across cycles


def detect_silence(
    *,
    now: Optional[float] = None,
    stale_threshold_s: float = DEFAULT_STALE_THRESHOLD_S,
    frozen_window_cycles: int = DEFAULT_FROZEN_WINDOW_CYCLES,
    db_path=None,
) -> Dict[str, Optional[str]]:
    """Decide which silence kinds (if any) are currently active.

    Returns a dict mapping every kind in ``ALL_KINDS`` to either:
    - a non-None ``reason`` string when the kind is active, or
    - ``None`` when the kind is NOT active.

    Caller (the watchdog loop) uses this output to open or close
    blackout rows. Pure function — no DB writes here.
    """
    from monitoring import fleet_history

    now = now if now is not None else time.time()
    out: Dict[str, Optional[str]] = {k: None for k in ALL_KINDS}

    latest = fleet_history.query_latest_heartbeat(db_path=db_path)
    if latest is None:
        out[KIND_NO_DATA] = "heartbeat table is empty"
        # daemon_dead is independent of heartbeat data — evaluate it even here,
        # else an empty/reset table would wrongly close a valid daemon_dead
        # blackout (B-F2, honest_failure #2). role_drift is likewise independent.
        out[KIND_DAEMON_DEAD] = _daemon_dead_reason()
        out[KIND_ROLE_DRIFT] = _role_drift_reason()
        out[KIND_MINI_DEAD] = _mini_dead_reason(now)
        return out

    # Guard the ts cast + clamp a future/negative age. A malformed heartbeat ts
    # would raise (aborting detection); a future ts (clock step / forged) yields
    # a negative age that shouldn't read as a bare "fresh forever". (QA audit.)
    try:
        ts = float(latest["ts"])
    except (TypeError, ValueError, KeyError):
        ts = now
    age = max(0.0, now - ts)
    if age > stale_threshold_s:
        out[KIND_HTTP_DEAD] = (
            f"latest heartbeat is {age:.0f}s old "
            f"(threshold {stale_threshold_s:.0f}s)"
        )

    # Frozen check: pull the last N heartbeats and judge uptime_s
    # advancement across them. If we don't have N rows yet, we abstain —
    # too early to call it.
    recent = fleet_history.query_heartbeat_history(
        since=now - stale_threshold_s * 6,  # generous window
        until=now,
        resolution_s=60,
        db_path=db_path,
    )
    if len(recent) >= frozen_window_cycles:
        tail = recent[-frozen_window_cycles:]
        uptimes = [r.get("uptime_s") for r in tail]
        present = [u for u in uptimes if u is not None]
        # A definitive "frozen" verdict must rest on POSITIVE evidence —
        # an uptime_s we actually observed and saw NOT advance — never on
        # the mere ABSENCE of a value. A single degraded cycle drops a
        # NULL uptime_s into an otherwise-fresh heartbeat: the collector
        # records a row even when the /fleet/slo fetch times out (Pi-class
        # /fleet/slo can miss its budget under load), storing
        # slo.get("uptime_s")=None honestly rather than a fabricated 0.
        # Reading that lone gap as a freeze manufactures a false blackout
        # (honest_failure_modes #1/#2: absence of a sample is not evidence
        # of a stuck counter — the flapping "missing" pages this fixes).
        # Cases:
        #   - EVERY sample missing across the full window: the uptime
        #     source has been dark the whole window while heartbeats keep
        #     landing (their fresh ts hides it from http_dead) — a real
        #     signal we must NOT swallow (#9). Surface it, honestly named.
        #   - SOME (not all) missing: a partial/transient gap — too few
        #     observed samples to judge advancement; abstain.
        #   - All present, one unique value: the daemon hasn't ticked.
        #     THAT is frozen.
        #   - All present, decreasing (restart) or increasing: healthy;
        #     abstain (http_dead covers the restart gap).
        if not present:
            out[KIND_FROZEN] = "uptime_s missing in recent heartbeats"
        elif len(present) < frozen_window_cycles:
            pass  # partial gap — insufficient evidence, do not accuse
        else:
            unique = {round(u, 3) for u in present}
            if len(unique) == 1:
                out[KIND_FROZEN] = (
                    f"uptime_s stuck at {present[0]:.1f}s across last "
                    f"{frozen_window_cycles} heartbeats"
                )

    # Daemon-dead check: independent of the heartbeat-derived signals
    # above. Catches the failure mode where the map keeps serving
    # /fleet/* (so heartbeats land and uptime_s — sourced from the map
    # process — keeps incrementing) while meshanchor-daemon is down.
    # Hysteresis bias is "false negative for one cycle" rather than
    # "false positive on a 5s daemon restart" — the streak counter
    # advances only on confirmed-inactive reads.
    out[KIND_DAEMON_DEAD] = _daemon_dead_reason()

    # Role drift: independent of heartbeat data (reads the converge SSOT's
    # dry-run plan for this box's declared role). Hysteresis inside.
    out[KIND_ROLE_DRIFT] = _role_drift_reason()

    # mini_dead: independent of heartbeat data (reads the operator's mini
    # state file). Inert on a box with no mini installed. Hysteresis inside.
    out[KIND_MINI_DEAD] = _mini_dead_reason(now)

    return out


def reconcile_blackouts(
    decisions: Dict[str, Optional[str]],
    *,
    now: Optional[float] = None,
    db_path=None,
) -> Dict[str, Any]:
    """Open or close blackout rows to match ``decisions``. Returns a
    summary: ``{kind: 'opened'|'closed'|'no_change'}`` plus the row
    counts the writes produced. Idempotent — calling repeatedly with
    the same decisions is a no-op."""
    from monitoring import fleet_history

    now = now if now is not None else time.time()
    summary: Dict[str, str] = {}

    active_now = {row["kind"]: row for row in fleet_history.query_active_blackouts(db_path=db_path)}

    for kind in ALL_KINDS:
        reason = decisions.get(kind)
        was_active = kind in active_now
        if reason is not None and not was_active:
            fleet_history.record_blackout_started(
                kind, reason=reason, ts=now, db_path=db_path,
            )
            summary[kind] = "opened"
        elif reason is None and was_active:
            fleet_history.record_blackout_ended(
                kind, ts=now, db_path=db_path,
            )
            summary[kind] = "closed"
        else:
            summary[kind] = "no_change"
    return summary


# ──────────────────────────────────────────────────────────────────────
# Active paging (ntfy) — the "don't fall silent" charter, actively
# ──────────────────────────────────────────────────────────────────────

# Priority per blackout kind. The silence kinds are operational OUTAGES → page
# loud, matching MeshForge's ntfy tier. role_drift is latent legibility debt
# (degraded), NOT an outage: MeshForge deliberately does NOT page it (routes it
# to a side-effect-free escalation feed — "degraded, not an outage"). MeshAnchor
# has no escalation-feed tier, so it pages role_drift at "min" — quiet-but-visible
# in ntfy history, honoring the "degraded != alarm" intent without going fully
# dashboard-dark. (2026-07-18, reconciling the MF<->MA paging-policy divergence.)
# ntfy priorities: min < low < default < high < urgent.
_KIND_PRIORITY = {
    KIND_NO_DATA: "high",
    KIND_HTTP_DEAD: "high",
    KIND_FROZEN: "high",
    KIND_DAEMON_DEAD: "urgent",
    KIND_ROLE_DRIFT: "min",
    # The second brain being dead is serious (we lose cadence observation) but
    # NOT a platform outage like daemon_dead — the NOC keeps serving. "high".
    KIND_MINI_DEAD: "high",
}
_KIND_TAGS = {
    KIND_NO_DATA: ["warning"],
    KIND_HTTP_DEAD: ["warning"],
    KIND_FROZEN: ["snowflake"],
    KIND_DAEMON_DEAD: ["rotating_light"],
    KIND_ROLE_DRIFT: ["gear"],
    KIND_MINI_DEAD: ["brain"],
}


def _notify_blackout_transitions(
    decisions: Dict[str, Optional[str]], summary: Dict[str, Any],
) -> None:
    """Page ntfy on blackout OPEN/CLOSE transitions (not steady state).

    ``reconcile_blackouts`` reports ``opened``/``closed`` only on the edge (it is
    idempotent per kind), so this fires exactly once per transition — a persistent
    blackout never re-pages. A no-op when ntfy isn't configured (dashboard-only).
    Never raises: paging must not sink a watchdog cycle."""
    try:
        from utils.ntfy_notify import publish
    except Exception as e:  # import failure must not break the loop
        logger.debug("ntfy_notify import failed: %s", e)
        return
    for kind in ALL_KINDS:
        change = summary.get(kind)
        if change == "opened":
            reason = decisions.get(kind) or kind
            publish(
                f"[meshanchor-watchdog] {kind}",
                f"{kind}: {reason}",
                priority=_KIND_PRIORITY.get(kind, "high"),
                tags=_KIND_TAGS.get(kind, ["warning"]),
            )
        elif change == "closed":
            publish(
                f"[meshanchor-watchdog] cleared: {kind}",
                f"{kind}: condition cleared",
                priority="min",
                tags=["white_check_mark"],
            )


# ──────────────────────────────────────────────────────────────────────
# Loop
# ──────────────────────────────────────────────────────────────────────


def run_loop(
    *,
    interval_s: float = DEFAULT_INTERVAL_S,
    stale_threshold_s: float = DEFAULT_STALE_THRESHOLD_S,
    frozen_window_cycles: int = DEFAULT_FROZEN_WINDOW_CYCLES,
    db_path=None,
    stop_event=None,
    max_cycles: Optional[int] = None,
) -> int:
    """Watchdog main loop. Same shape as the collector's run_loop —
    interval-driven, stop-event responsive, max_cycles for tests.
    A stop_event is always in play (created internally if the caller
    didn't pass one) so the inter-cycle wait uses ``stop_event.wait``
    rather than ``time.sleep`` (CLAUDE.md MF010)."""
    import threading
    if stop_event is None:
        stop_event = threading.Event()

    cycles = 0
    while True:
        if stop_event.is_set():
            break
        if max_cycles is not None and cycles >= max_cycles:
            break
        cycle_start = time.monotonic()
        try:
            decisions = detect_silence(
                stale_threshold_s=stale_threshold_s,
                frozen_window_cycles=frozen_window_cycles,
                db_path=db_path,
            )
            summary = reconcile_blackouts(decisions, db_path=db_path)
            _notify_blackout_transitions(decisions, summary)
            cycles += 1
            active_kinds = [k for k, v in decisions.items() if v is not None]
            level = logger.warning if active_kinds else logger.info
            level(
                "cycle %d: active=%s reconcile=%s",
                cycles,
                ",".join(active_kinds) or "none",
                summary,
            )
        except Exception as e:
            logger.exception("watchdog cycle raised: %s", e)
        sleep_for = max(0.0, interval_s - (time.monotonic() - cycle_start))
        stop_event.wait(sleep_for)
    return cycles


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fleet_watchdog",
        description="MeshAnchor fleet silence watchdog — opens/closes "
                    "blackout rows when the collector goes silent or "
                    "the daemon freezes.",
    )
    p.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_S,
        help=f"Check cadence seconds (default: {DEFAULT_INTERVAL_S})",
    )
    p.add_argument(
        "--stale", type=float, default=DEFAULT_STALE_THRESHOLD_S,
        help=f"HTTP-dead threshold seconds (default: {DEFAULT_STALE_THRESHOLD_S})",
    )
    p.add_argument(
        "--frozen-window", type=int, default=DEFAULT_FROZEN_WINDOW_CYCLES,
        help=f"How many recent heartbeats must show advancing uptime_s "
             f"(default: {DEFAULT_FROZEN_WINDOW_CYCLES})",
    )
    p.add_argument("--db-path",
                   help="Override fleet_history DB path")
    p.add_argument("--once", action="store_true",
                   help="Run a single check cycle and exit")
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    if args.once:
        decisions = detect_silence(
            stale_threshold_s=args.stale,
            frozen_window_cycles=args.frozen_window,
            db_path=args.db_path,
        )
        summary = reconcile_blackouts(decisions, db_path=args.db_path)
        active = [k for k, v in decisions.items() if v is not None]
        logger.info("one-shot: active=%s reconcile=%s",
                    ",".join(active) or "none", summary)
        return 0

    import threading
    stop = threading.Event()

    def _on_signal(signum, frame):
        logger.info("received signal %d; stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    logger.info(
        "starting fleet watchdog interval=%.1fs stale=%.1fs frozen_window=%d",
        args.interval, args.stale, args.frozen_window,
    )
    cycles = run_loop(
        interval_s=args.interval,
        stale_threshold_s=args.stale,
        frozen_window_cycles=args.frozen_window,
        db_path=args.db_path,
        stop_event=stop,
    )
    logger.info("watchdog stopped after %d cycles", cycles)
    return 0


if __name__ == "__main__":
    sys.exit(main())
