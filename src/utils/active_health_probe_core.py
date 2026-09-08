"""Health-probe primitives — systemd unit resolution + /proc fd accounting.

Split out of ``active_health_probe.py`` on 2026-08-12: that file stood at
1,449 of the 1,500-line MF025 cap, and the tri-state MainPID port needed room.
Split the file, never raise the cap (CLAUDE.md) — and never shave the record
down to fit either, which is what the first two attempts at this did.

The split mirrors MeshForge, the lead repo for this arc: these exact
primitives live in its ``utils/watchdog_probe_core.py``, apart from the
probes that consume them. Import via ``active_health_probe``, not from here.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional, Tuple


# Soft RLIMIT_NOFILE line in /proc/<pid>/limits, e.g.:
#   Max open files            1024                 524288               files
_LIMITS_NOFILE_RE = re.compile(
    r"^Max open files\s+(\d+|unlimited)\s+(\d+|unlimited)", re.MULTILINE
)


def _resolve_main_pid_status(
    service_name: str, *, systemctl_path: str = "systemctl",
) -> Tuple[str, Optional[int]]:
    """Four-state MainPID resolution: ``(status, pid)``.

    ``ok`` (running, pid > 1) · ``down`` (unit EXISTS here but has no MainPID
    — ``check_systemd_service`` owns that) · ``absent`` (no such unit on this
    box, ``LoadState=not-found``) · ``unknown`` (systemctl could not be run or
    parsed — unobservable, and NEVER "absent").

    MeshForge parity port, 2026-08-12; MF is the lead repo for this arc and
    carries the full account (``utils/watchdog_probe_core.py`` + its
    persistent_issues archive). There, four watchdog classes read
    ``indeterminate`` forever on meshanchor-server because the flat form
    collapsed absent, down and unobservable into one ``None``, and every
    consumer turned it into "meshtasticd is inactive; ``service_inactive``
    owns that" — a handoff to a probe that cannot own a unit which does not
    exist. Here the collapse was quieter but the same shape: all three no-pid
    cases returned ``healthy=True, reason="inactive_or_unresolved"``, so an
    unobservable systemctl was indistinguishable from a unit absent by design
    in the one artifact an operator reads.

    Measured live: ``systemctl show`` exits 0 in ALL these cases, so rc
    carries no signal — ``LoadState`` does, and rides the SAME subprocess.
    Parsed ``KEY=value``, not ``--value``: systemd emits properties in its own
    canonical order, so positional parsing would mis-pair the two facts.
    """
    try:
        proc = subprocess.run(
            [systemctl_path, "show", "-p", "MainPID", "-p", "LoadState",
             service_name],
            capture_output=True, text=True, timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ("unknown", None)
    if proc.returncode != 0:
        return ("unknown", None)
    props = {}
    for line in (proc.stdout or "").splitlines():
        key, sep, val = line.partition("=")
        if sep:
            props[key.strip()] = val.strip()
    raw_pid = props.get("MainPID")
    if raw_pid is None:
        return ("unknown", None)
    try:
        pid = int(raw_pid)
    except (ValueError, TypeError):
        return ("unknown", None)
    if pid > 1:
        return ("ok", pid)
    # Only an explicit not-found proves absence; a missing/odd LoadState
    # (older systemd) falls back to the pre-split meaning, the conservative one.
    if props.get("LoadState") == "not-found":
        return ("absent", None)
    return ("down", None)


# NOTE: the flat ``_resolve_main_pid`` was DELETED here, not kept as a shim.
# It had exactly one caller in this tree and that caller now takes the status
# form, so a shim would be a footgun sitting on the module surface with no
# user — the "dead exemption reads as sanctioned" shape. MeshForge keeps its
# shim only because its probe hub re-exports the name.


def _read_fd_usage(pid: int, *, proc_root: str = "/proc"):
    """Return ``(open_fd_count, soft_limit)`` for ``pid`` or None.

    Counts ``/proc/<pid>/fd`` entries and parses the *soft* ``Max open
    files`` column from ``/proc/<pid>/limits`` — the soft limit is the one a
    process actually hits ([Errno 24]). Returns None on any read failure
    (process vanished, permission, unlimited soft limit) so an unreadable
    target never alarms. Module-level so tests can build a fake /proc tree.
    """
    fd_dir = Path(proc_root) / str(pid) / "fd"
    limits_path = Path(proc_root) / str(pid) / "limits"
    try:
        open_count = sum(1 for _ in os.scandir(fd_dir))
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None
    try:
        limits_text = limits_path.read_text()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    m = _LIMITS_NOFILE_RE.search(limits_text)
    if not m:
        return None
    soft_raw = m.group(1)
    if soft_raw == "unlimited":
        return None
    try:
        soft = int(soft_raw)
    except (ValueError, TypeError):
        return None
    if soft <= 0:
        return None
    return open_count, soft


# ── rnstatus-timeout confirmation (2026-09-08, ported from MeshForge) ──
#
# A single `rnstatus` run exceeding its bound is NOT evidence that rnsd's
# RPC is wedged. `rnstatus` is a fresh interpreter that imports RNS
# before it speaks one byte of RPC, so its WALL TIME measures the box's
# CPU/IO headroom at least as much as rnsd's health. Measured on the
# MeshForge fleet, 2026-09-08:
#
#   06:36:34  apt-daily-upgrade starts (61s CPU, 273 MB, 35 MB swap)
#   06:37:41  rnstatus timed out  ->  "RPC round-trip is wedged"
#   06:37:49  ...while the gateway logged rpc[rnsd.path_table_read] ok
#             0.000s every 10s, straight THROUGH the declared "wedge"
#   06:37:53  apt-daily-upgrade finishes
#   06:38:26  cleared — nothing done, nothing wrong
#
# rnsd's RPC was sub-millisecond at the exact second we called it wedged:
# a correctly-derived claim about the wrong quantity.
#
# ⚠️ Dangerous rather than merely noisy: the reason tells the operator to
# restart rnsd, and rapid rnsd restart cycling is what opens the `@rns`
# ownership race (#69).
#
# Discriminator: PERSISTENCE. A real #68/#72 wedge holds until rnsd is
# restarted; contention passes within a tick. Require N consecutive
# timed-out observations before reporting unhealthy; short of that the
# check reports healthy=True with an explicitly UNCONFIRMED reason, so
# the moment is never silently laundered into a clean "rpc_responsive".
#
# Module-level (not instance) state on purpose: it must survive a prober
# that is re-instantiated per tick, and it must NOT live on disk — an
# unwritable state dir would freeze the streak below its threshold and
# the check could never fire at all (the 2026-09-02 debounce-saver trap).
_RPC_CONFIRM_TICKS_ENV = "MESHANCHOR_RNS_RPC_CONFIRM_TICKS"
_DEFAULT_RPC_CONFIRM_TICKS = 3

_rpc_timeout_streak = 0


def _rpc_confirm_ticks() -> int:
    """Consecutive timed-out checks required before claiming a wedge."""
    raw = os.environ.get(_RPC_CONFIRM_TICKS_ENV)
    if raw is None:
        return _DEFAULT_RPC_CONFIRM_TICKS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_RPC_CONFIRM_TICKS
    return value if value >= 1 else _DEFAULT_RPC_CONFIRM_TICKS


def reset_rns_rpc_timeout_streak() -> None:
    """Clear the consecutive-timeout streak. For tests and cold start."""
    global _rpc_timeout_streak
    _rpc_timeout_streak = 0


def _cpu_pressure_context() -> str:
    """Best-effort load/PSI string carried WITH the wedge claim, so the
    operator can tell "rnsd is wedged" from "this Pi was buried" on the
    alert itself rather than from journals afterwards."""
    parts = []
    try:
        with open("/proc/loadavg", "r") as fh:
            parts.append("loadavg " + " ".join(fh.read().split()[:3]))
    except OSError:
        parts.append("loadavg unknown")
    try:
        with open("/proc/pressure/cpu", "r") as fh:
            for line in fh:
                if line.startswith("some"):
                    for tok in line.split():
                        if tok.startswith("avg10="):
                            parts.append("cpu-psi-some10 " + tok[6:])
                    break
    except OSError:
        pass
    return "; ".join(parts)


def bump_rns_rpc_timeout_streak() -> int:
    """Record one more consecutive timed-out check; return the new streak."""
    global _rpc_timeout_streak
    _rpc_timeout_streak += 1
    return _rpc_timeout_streak


def judge_rns_rpc_timeout(timed_out: bool) -> Tuple[bool, str]:
    """Turn one `rnstatus` outcome into a (healthy, reason) verdict.

    Owns the consecutive-timeout streak, so the decision and the state it
    depends on cannot drift apart. See the block comment above for why a
    single timeout is not a wedge.
    """
    global _rpc_timeout_streak
    if not timed_out:
        _rpc_timeout_streak = 0
        return True, "rpc_responsive"

    _rpc_timeout_streak += 1
    needed = _rpc_confirm_ticks()
    pressure = _cpu_pressure_context()

    if _rpc_timeout_streak < needed:
        # Not yet decidable. Healthy so we do not page, but the reason says
        # UNCONFIRMED — never the bare "rpc_responsive" that would launder a
        # genuinely blind moment into health.
        return True, (
            f"rpc_timeout_unconfirmed {_rpc_timeout_streak}/{needed}"
            f" ({pressure})"
        )[:120]

    return False, (
        f"rns_rpc_unresponsive: rnstatus timed out on {_rpc_timeout_streak} "
        f"consecutive checks — RPC round-trip wedged ({pressure}). "
        "CONFIRM first: timeout 8 rnstatus; then restart rnsd.service + "
        "RNS-using services."
    )[:240]
