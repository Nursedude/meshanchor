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
