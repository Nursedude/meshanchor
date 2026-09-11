"""Host-level health checks — dep floors, fd exhaustion, user timers.

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
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.user_units import enabled_user_timers
from utils.active_health_probe_core import (
    HealthResult, _read_fd_usage, _resolve_main_pid_status,
)

#: Alias kept so the twins do not re-implement enrolment (hfm #5).
_enabled_user_timers = enabled_user_timers

logger = logging.getLogger(__name__)

# ── user-timer failure detection (MeshForge parity port, 2026-07-19) ──
# systemd's user manager logs both of these about a unit under the
# ``USER_UNIT=`` journal field, which root can select WITHOUT sudo or the
# user bus. Verified empirically on the fleet before this was trusted: had
# the success line lacked that field, success-detection would have been
# silently dead and the check would alarm after every recovery.
_USER_TIMER_FAIL_PATTERN = "Failed with result"
_USER_TIMER_OK_PATTERN = "Finished "



def _journal_user_unit_ts(
    user_unit: str,
    pattern: str,
    lookback: str,
    journalctl_path: str = "journalctl",
) -> Optional[List[float]]:
    """Epoch timestamps of ``USER_UNIT=<user_unit>`` journal lines matching
    ``pattern`` within ``lookback``.

    ``-u <unit>`` selects the SYSTEM journal namespace and is structurally
    blind to user units (rc 0 but EMPTY from a root context) — the
    ``USER_UNIT=`` field selector is the read that actually works.

    Returns the parsed list (``[]`` = genuinely no matching lines) or
    **None** on journalctl unavailable / timeout / rc∉(0,1) — the honest
    *unobservable* answer, which a caller must never collapse into ``[]``.
    """
    try:
        proc = subprocess.run(
            [
                journalctl_path, "-q", f"USER_UNIT={user_unit}",
                "--since", f"-{lookback}", "-g", pattern,
                "-o", "short-unix", "--no-pager",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode not in (0, 1):
        return None
    out: List[float] = []
    for ln in proc.stdout.splitlines():
        if not ln:
            continue
        head = ln.split(None, 1)[0]
        try:
            out.append(float(head))
        except ValueError:
            continue
    return out


def _journal_user_unit_has_lines(
    user_unit: str,
    lookback: str,
    journalctl_path: str = "journalctl",
) -> Optional[bool]:
    """Does ``USER_UNIT=<user_unit>`` have ANY journal line in ``lookback``?

    The COVERAGE question for the reader above (2026-08-13, MeshForge parity).
    That reader honestly returns ``[]`` for "journalctl ran and nothing
    matched" — but ``[]`` also comes back when the unit has NO lines in the
    window at all, so a caller cannot tell "the job ran and logged no failures"
    from "nothing about this unit is visible here".

    **Measured on meshanchor-server**: of four enrolled timers, two returned
    empty for BOTH patterns and were folded into an affirmative
    ``user_timers_ok_4``. One of them, ``meshanchor-map-restart.service``,
    is a DAILY timer that had fired 19h earlier — comfortably outside the 3h
    lookback, so "no failures" was never an observation about it (the
    slow-cadence residual this module's header documents). The other two units
    did have lines and were genuinely judged.

    ⚠️ Do NOT justify this by "the user journal is dark on that box".
    ``journalctl --user`` there reports *No journal files were found*, but that
    is the per-user client path; the root ``USER_UNIT=`` selector this module
    uses works fine and returns lines for the units that logged any. Two
    different access routes — checked 2026-08-13 after an earlier read of mine
    conflated them.

    A unit that logged in the window is judgeable; one that logged nothing is
    not. Returns True (lines present), False (none at all — cannot judge), or
    **None** unobservable. Callers must treat both False and None as "say
    nothing about this unit", never as healthy (honest_failure_modes #2).

    Asked ONLY when both pattern queries came back empty, so a busy box pays
    nothing extra.
    """
    try:
        proc = subprocess.run(
            [
                journalctl_path, "-q", f"USER_UNIT={user_unit}",
                "--since", f"-{lookback}", "-n", "1", "-o", "cat",
                "--no-pager",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode not in (0, 1):
        return None
    return bool(proc.stdout.strip())


def check_dep_version_floor(
    watched,
    *,
    requirements_path=None,
    installed: Optional[Dict[str, str]] = None,
) -> HealthResult:
    """A critical pip dependency importable by THIS process sits BELOW the
    requirements/core.txt floor — the box missed or failed an update
    (MeshForge ``probe_dep_version_drift`` parity, 2026-07-03).

    Consumer-of-record simplification vs MeshForge: MF's watchdog runs in
    a DIFFERENT process/user than the services, so it must enumerate
    venv/user-site/system-dist installs. This check runs INSIDE the
    MeshAnchor daemon — the very interpreter that imports meshtastic — so
    ``importlib.metadata`` on our own env IS the consumer-of-record. The
    read hits on-disk dist-info each tick, so a pip upgrade clears the
    alarm as soon as it lands (before the restart that loads it).

    Self-guards healthy-with-reason (this codebase's idiom for
    not-applicable, cf. check_fd_exhaustion): no parseable floor
    (unreadable SSOT must not read as compliant OR as drift — it is
    indeterminate), or no watched package importable here (a venv
    elsewhere may be the consumer — don't guess). Fires unhealthy only on
    a concrete below-floor fact.
    """
    from utils.requirements_floor import (
        default_core_requirements,
        read_requirement_floors,
        version_below,
    )

    req = (Path(requirements_path) if requirements_path
           else default_core_requirements())
    floors = read_requirement_floors(watched, req)
    if not floors:
        return HealthResult(
            healthy=True, reason="dep_floor_indeterminate_no_floor")

    if installed is None:
        import importlib.metadata as _md
        installed = {}
        for pkg in floors:
            try:
                installed[pkg] = _md.version(pkg)
            except _md.PackageNotFoundError:
                continue  # not visible in this env — don't guess
            except Exception:
                continue
    if not installed:
        return HealthResult(
            healthy=True, reason="dep_floor_indeterminate_not_importable")

    stale = [
        f"{pkg} installed={installed[pkg]} floor>={floor}"
        for pkg, floor in floors.items()
        if pkg in installed and version_below(installed[pkg], floor)
    ]
    if stale:
        return HealthResult(
            healthy=False,
            reason=(
                f"below_requirements_floor: {'; '.join(stale)} — this box "
                f"missed or failed an update; fix: sudo pip3 install "
                f"--break-system-packages '<pkg>==<floor>' then restart "
                f"meshanchor (see feedback_version_env_rigor)"
            ),
        )
    ok = ", ".join(f"{p}={v}" for p, v in sorted(installed.items()))
    return HealthResult(healthy=True, reason=f"dep_floor_ok {ok}")


def check_fd_exhaustion(
    service_name: str,
    *,
    proc_root: str = "/proc",
    systemctl_path: str = "systemctl",
    degraded_ratio: float = 0.80,
    wedge_ratio: float = 0.95,
    main_pid: Optional[int] = None,
) -> HealthResult:
    """Warn when a service's open fds approach its soft RLIMIT_NOFILE.

    Proactive companion to the HTTP/port wedge checks (which only fail
    once the port has already gone dark). MeshForge Issue #73
    (2026-05-31): meshanchor-map leaked one paho MQTT client socket per
    reconnect until it hit the 1024 soft fd cap; new ``accept()`` then
    failed with ``[Errno 24]`` and ``:5000`` wedged — unservable for ~1h
    before any wedge check fired. Counting fds vs the soft limit surfaces
    the climb BEFORE the wedge, and names the fd-leak cause.

    Unhealthy past ``degraded_ratio`` (default 80%); the reason flags
    ``wedge`` past ``wedge_ratio`` (default 95% — exhaustion imminent).
    Healthy (and quiet) when the service is absent/inactive
    (``check_systemd_service`` owns down), /proc is unreadable, the soft
    limit is unlimited, or usage is below the degraded threshold — a
    healthy process must not false-alarm.

    ⚠️ The three no-pid cases carry DISTINCT reasons (2026-08-12, MF
    parity). They used to share ``inactive_or_unresolved`` — said of a box
    with no such unit AND of a systemctl we could not run — and
    ``last_result.reason`` in ``daemon_status.json`` is the ONLY place an
    operator sees this, so the collapse made real blindness unreadable.
    All three stay ``healthy=True`` deliberately: no third state exists
    here, and turning a transient systemctl timeout into an alarm would
    flap the hysteresis. Legible reason, not an invented page.
    """
    if main_pid is not None:
        pid_status, pid = "ok", main_pid
    else:
        pid_status, pid = _resolve_main_pid_status(
            service_name, systemctl_path=systemctl_path
        )
    if pid is None:
        return HealthResult(healthy=True, reason={
            # no unit here at all → no fd table to count (inert)
            "absent": f"absent_no_unit ({service_name})",
            # unit exists, stopped → check_systemd_service owns it
            "down": "inactive_check_systemd_service_owns",
        }.get(pid_status, f"unit_state_unobservable ({service_name})"))

    usage = _read_fd_usage(pid, proc_root=proc_root)
    if usage is None:
        return HealthResult(healthy=True, reason="fd_usage_unreadable")
    open_count, soft = usage

    ratio = open_count / soft
    if ratio < degraded_ratio:
        return HealthResult(
            healthy=True,
            reason=f"fd_ok_{open_count}/{soft}",
        )

    level = "wedge" if ratio >= wedge_ratio else "degraded"
    return HealthResult(
        healthy=False,
        reason=(
            f"fd_exhaustion ({level}): {service_name} (pid {pid}) holds "
            f"{open_count}/{soft} open fds ({ratio * 100:.0f}% of soft "
            f"RLIMIT_NOFILE). Approaching [Errno 24] — new sockets/files "
            f"will fail and the HTTP server will stop accepting (#73 "
            f"fd-leak class). Inspect: sudo ls /proc/{pid}/fd | wc -l ; "
            f"sudo ss -tanp | grep pid={pid}"
        ),
    )


def check_user_timer_unit_failing(
    *,
    user_home: Optional[str] = None,
    lookback: str = "3h",
    min_failures: int = 2,
    recency_s: float = 3600.0,
    journalctl_path: str = "journalctl",
    ts_fn=None,
    coverage_fn=None,
    now: Optional[float] = None,
) -> HealthResult:
    """Unhealthy when an enabled USER *timer's* job fails on every firing.

    MeshForge ``probe_user_timer_unit_failing`` parity port (2026-07-19).
    Origin incident is MF-side but the blind spot is identical on both
    NOCs: a timer-triggered oneshot is **inactive between firings by
    design**, so nothing that judges "is it running" can judge it, and it
    never crashloops, so restart-counter detectors miss it too. On
    MeshForge, kiai's ``meshforge-tracer.timer`` fired every 10 minutes
    for a week while its job exited 2 every time, and no probe on either
    repo could have seen it.

    Outcome-based rather than an error count: a timer's service is judged
    failing only when it has ``min_failures`` ``Failed with result``
    events inside ``lookback``, the newest fresher than ``recency_s``,
    **and no successful run since that newest failure**. Fails-then-
    succeeds is a blip and stays quiet; a remediated job stops alarming
    immediately instead of ringing off its own history.

    Reads are bus-free and root-readable: enrollment from
    ``~/.config/systemd/user/timers.target.wants/`` symlinks, outcomes
    from the ``USER_UNIT=`` journal field (no sudo, no user bus).

    Self-guards healthy-with-reason, following the fd-exhaustion
    precedent, so boxes with no user timers never false-alarm: no
    resolvable operator, no timers enrolled, wants dir unreadable, or the
    journal unobservable for every enrolled timer.

    NOTE (calibrated — the MF twin is stricter): MeshForge's version is
    tri-state and HOLDS its debounce streak across an unobservable tick,
    so a journalctl wedge cannot clear an in-flight outage. ``HealthResult``
    is binary, so here an unobservable journal necessarily reads healthy
    (``journal_unobservable``) and the probe's own ``fails`` hysteresis is
    what rides out a single bad tick. The reason string is deliberately
    greppable so an operator can tell "observed clean" from "could not
    look".
    """
    now = time.time() if now is None else now

    if user_home is None:
        operator = None
        try:
            from utils.fleet_test_runner import _find_operator_user
            operator = _find_operator_user()
        except Exception:
            operator = None
        if operator is None:
            return HealthResult(healthy=True, reason="no_operator_user")
        uid, name = operator
        try:
            import pwd as _pwd
            user_home = _pwd.getpwuid(uid).pw_dir
        except (ImportError, KeyError):
            user_home = f"/home/{name}"

    timers = _enabled_user_timers(user_home)
    if timers is None:
        return HealthResult(healthy=True, reason="user_timers_unreadable")
    if not timers:
        return HealthResult(healthy=True, reason="no_user_timers_enrolled")

    if ts_fn is None:
        def ts_fn(unit, pattern):
            return _journal_user_unit_ts(
                unit, pattern, lookback, journalctl_path=journalctl_path)

    if coverage_fn is None:
        def coverage_fn(unit):
            return _journal_user_unit_has_lines(
                unit, lookback, journalctl_path=journalctl_path)

    failing = []
    observed_any = False
    observed_count = 0
    for timer, service in sorted(timers.items()):
        fails = ts_fn(service, _USER_TIMER_FAIL_PATTERN)
        oks = ts_fn(service, _USER_TIMER_OK_PATTERN)
        if fails is None or oks is None:
            continue                       # unobservable for THIS unit
        if not fails and not oks:
            # AMBIGUOUS (2026-08-13, MeshForge parity): "ran and logged
            # nothing matching" and "nothing about this unit is visible in
            # the window" are the same empty result. Measured on
            # meshanchor-server: 2 of 4 enrolled timers were empty for both
            # patterns and got folded into an affirmative
            # `user_timers_ok_4` — one being a DAILY timer that fired 19h
            # ago, outside the 3h lookback entirely. Ask the unfiltered
            # question before reading silence as health.
            if coverage_fn(service) is not True:
                continue           # dead/unreadable channel — say nothing
        observed_any = True
        observed_count += 1
        if len(fails) < min_failures:
            continue
        newest_fail = max(fails)
        if (now - newest_fail) > recency_s:
            continue                       # already remediated
        if oks and max(oks) > newest_fail:
            continue                       # recovered after the failures
        failing.append((service, len(fails)))

    if not observed_any:
        return HealthResult(healthy=True, reason="journal_unobservable")
    if not failing:
        # ⚠️ Say how many were actually JUDGED, not how many are enrolled
        # (2026-08-13). A label may claim only what its evidence covers:
        # on meshanchor-server 2 of 4 units had no journal line in the
        # window (a daily timer that fired 19h ago is legitimately outside
        # a 3h lookback — the KNOWN slow-cadence residual), and reporting
        # a flat "ok_4" asserted health over two units nothing had looked
        # at. Same defect class as the verdict that said "no mini" about a
        # box when it had only checked one file path.
        return HealthResult(
            healthy=True,
            reason=(f"user_timers_ok_{observed_count}_of_{len(timers)}"
                    if observed_count != len(timers)
                    else f"user_timers_ok_{len(timers)}"),
        )

    listed = ", ".join(f"{svc} ({n}x in {lookback})" for svc, n in failing)
    return HealthResult(
        healthy=False,
        reason=(
            f"user_timer_unit_failing: timer-triggered user job(s) "
            f"failing every firing: {listed}. No successful run since the "
            f"newest failure. These are invisible to every 'is it running' "
            f"check — a oneshot is inactive between firings by design and "
            f"never crashloops. Inspect: systemctl --user status <unit> ; "
            f"journalctl --user -u <unit> -n 50. Usual cause is a missing "
            f"input (config/peers file), which fails every cadence forever "
            f"without alerting anyone."
        ),
    )
