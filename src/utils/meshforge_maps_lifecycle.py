"""Lifecycle helpers for the meshforge-maps systemd unit (Phase 6.2).

Phase 6 / 6.1 / 6.3 made meshforge-maps *visible* (probe + browser open + data
fusion + endpoint config). Phase 6.2 makes it *controllable* — start, stop,
restart of the local ``meshforge-maps.service`` systemd unit, surfaced through
an explicit user action in the TUI.

Constraints inherited from upstream rules:

* **Issue #31** — never make persistent system changes silently on startup.
  Lifecycle here only ever fires from an explicit menu choice + double-confirm
  dialog, never from a daemon loop or auto-recovery path.
* **Localhost only** — `sudo systemctl start <unit>` only meaningfully targets
  the local host. If the user has pointed the maps endpoint at a remote box
  (Phase 6.3), :func:`is_localhost` returns False and the handler refuses the
  action with a clear message.
* **Unit-existence pre-check** — if ``meshforge-maps.service`` is not
  installed on this host, we refuse with a fix hint pointing at the install
  README rather than letting systemctl emit a generic "Unit not found" line.
* **Single source of truth** — all systemctl calls flow through
  :mod:`utils.service_check` (which uses ``_sudo_cmd`` for privilege
  escalation, has bounded timeouts, and never raises). This module is a
  policy layer on top, not a parallel implementation.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple

from utils.service_check import (
    _sudo_cmd,
    restart_service,
    start_service,
    stop_service,
)

logger = logging.getLogger(__name__)


SYSTEMD_UNIT = "meshforge-maps"
"""Systemd unit name (without the .service suffix). Matches the install
script's expectation in the meshforge-maps repo."""

INSTALL_HINT_URL = "https://github.com/Nursedude/meshforge-maps"

LOCALHOST_ALIASES = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})
"""Hostnames we treat as 'this machine'. Anything else is remote and
lifecycle is refused (we can't sudo on someone else's box)."""


@dataclass(frozen=True)
class LifecycleResult:
    """Outcome of a lifecycle action.

    ``success`` is the load-bearing field — handlers render a green tick or
    a red cross based on it. ``action`` echoes back what was attempted
    (``"start"`` / ``"stop"`` / ``"restart"``) so a single rendering helper
    can label the result without the caller needing to remember context.
    ``message`` is human-readable and may include the systemctl error tail.
    """

    success: bool
    action: str
    message: str


@dataclass(frozen=True)
class UnitStatus:
    """Snapshot of the systemd unit's active/enabled state for display.

    All three fields are best-effort: a missing systemctl, a non-systemd
    host, or a permission glitch yields a ``UnitStatus`` with ``installed``
    False and the other booleans None. The TUI renders ``"unknown"`` for
    None instead of guessing.
    """

    installed: bool
    active: Optional[bool]
    enabled: Optional[bool]
    sub_state: Optional[str] = None
    error: Optional[str] = None


def is_localhost(host: str) -> bool:
    """Return True iff ``host`` refers to this machine.

    Used by the TUI to decide whether to even show lifecycle rows. We don't
    try DNS resolution here — that opens a slow network call on every menu
    render. Hostname literal match against :data:`LOCALHOST_ALIASES` is
    sufficient because the user just configured this string in Phase 6.3's
    endpoint dialog (so we know what they typed).
    """
    if not host:
        return False
    return host.strip().lower() in LOCALHOST_ALIASES


def is_unit_installed(unit: str = SYSTEMD_UNIT, timeout: int = 5) -> Tuple[bool, Optional[str]]:
    """Check whether ``<unit>.service`` exists on this host.

    Returns ``(installed, error)``. ``error`` is None on a clean detection
    (whether installed or not); populated when systemctl is unreachable so
    the caller can distinguish "not installed" from "couldn't tell".
    """
    try:
        result = subprocess.run(
            ["systemctl", "list-unit-files", f"{unit}.service"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return False, "systemctl not found (is this a systemd host?)"
    except subprocess.TimeoutExpired:
        return False, f"timeout while listing unit files for {unit}"
    except OSError as e:
        return False, f"could not list unit files: {e}"

    # systemctl list-unit-files prints the unit name in the table when it
    # exists. A missing unit returns "0 unit files listed." with the unit
    # name absent from stdout.
    return (unit in result.stdout), None


def get_unit_status(unit: str = SYSTEMD_UNIT, timeout: int = 5) -> UnitStatus:
    """Pull is-active / is-enabled / SubState for ``<unit>.service``.

    Best-effort — never raises. If systemctl can't be reached we return a
    ``UnitStatus(installed=False, error=...)`` that the TUI can render as
    "unknown" without crashing.
    """
    installed, install_err = is_unit_installed(unit, timeout=timeout)
    if install_err is not None:
        return UnitStatus(installed=False, active=None, enabled=None, error=install_err)
    if not installed:
        return UnitStatus(installed=False, active=False, enabled=False)

    active = _systemctl_bool(["systemctl", "is-active", unit], timeout=timeout)
    enabled = _systemctl_bool(["systemctl", "is-enabled", unit], timeout=timeout)
    sub_state = _systemctl_property(unit, "SubState", timeout=timeout)
    return UnitStatus(
        installed=True,
        active=active,
        enabled=enabled,
        sub_state=sub_state,
    )


def _systemctl_bool(cmd, timeout: int = 5) -> Optional[bool]:
    """Return True/False for a systemctl is-* probe; None on subprocess error."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return result.returncode == 0


def _systemctl_property(unit: str, prop: str, timeout: int = 5) -> Optional[str]:
    """Read a single ``systemctl show`` property. None on subprocess error."""
    try:
        result = subprocess.run(
            ["systemctl", "show", unit, f"--property={prop}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    out = result.stdout.strip()
    if "=" not in out:
        return None
    return out.split("=", 1)[1] or None


def _refuse_remote(host: str, action: str) -> LifecycleResult:
    return LifecycleResult(
        success=False,
        action=action,
        message=(
            f"Lifecycle control is local-only. Maps endpoint is configured "
            f"for {host!r}, which isn't this machine. Use the host's own "
            f"NOC to start/stop the meshforge-maps service."
        ),
    )


def _refuse_missing_unit(action: str) -> LifecycleResult:
    return LifecycleResult(
        success=False,
        action=action,
        message=(
            f"meshforge-maps.service is not installed on this host.\n"
            f"  Install: {INSTALL_HINT_URL}"
        ),
    )


def start(host: str = "localhost", timeout: int = 30) -> LifecycleResult:
    """Start the local meshforge-maps systemd unit (explicit user action)."""
    refusal = _gate(host, "start")
    if refusal is not None:
        return refusal
    success, msg = start_service(SYSTEMD_UNIT, timeout=timeout)
    return LifecycleResult(success=success, action="start", message=msg)


def stop(host: str = "localhost", timeout: int = 30) -> LifecycleResult:
    """Stop the local meshforge-maps systemd unit (explicit user action)."""
    refusal = _gate(host, "stop")
    if refusal is not None:
        return refusal
    success, msg = stop_service(SYSTEMD_UNIT, timeout=timeout)
    return LifecycleResult(success=success, action="stop", message=msg)


def restart(host: str = "localhost", timeout: int = 30) -> LifecycleResult:
    """Restart the local meshforge-maps systemd unit (explicit user action)."""
    refusal = _gate(host, "restart")
    if refusal is not None:
        return refusal
    success, msg = restart_service(SYSTEMD_UNIT, timeout=timeout)
    return LifecycleResult(success=success, action="restart", message=msg)


def _gate(host: str, action: str) -> Optional[LifecycleResult]:
    """Apply the localhost + unit-existence guards. Returns a refusal
    LifecycleResult when the action shouldn't proceed; None otherwise."""
    if not is_localhost(host):
        return _refuse_remote(host, action)
    installed, err = is_unit_installed()
    if err is not None:
        return LifecycleResult(success=False, action=action, message=err)
    if not installed:
        return _refuse_missing_unit(action)
    return None


def format_unit_status(status: UnitStatus) -> str:
    """Render a UnitStatus as a multi-line block for the TUI.

    Pure function so it's trivially unit-testable. Uses "unknown" for None
    fields rather than guessing, mirroring the Phase 6 ``_format_status``
    convention.
    """
    if not status.installed:
        if status.error:
            return (
                f"  meshforge-maps.service: UNKNOWN\n"
                f"    {status.error}"
            )
        return (
            f"  meshforge-maps.service: NOT INSTALLED\n"
            f"    Install: {INSTALL_HINT_URL}"
        )

    active_label = _bool_label(status.active, "active", "inactive")
    enabled_label = _bool_label(status.enabled, "enabled", "disabled")
    lines = [
        f"  meshforge-maps.service: {active_label.upper()}",
        f"    Enabled at boot: {enabled_label}",
    ]
    if status.sub_state:
        lines.append(f"    SubState:        {status.sub_state}")
    return "\n".join(lines)


def _bool_label(value: Optional[bool], yes: str, no: str) -> str:
    if value is None:
        return "unknown"
    return yes if value else no


__all__ = [
    "INSTALL_HINT_URL",
    "LOCALHOST_ALIASES",
    "LifecycleResult",
    "SYSTEMD_UNIT",
    "UnitStatus",
    "format_unit_status",
    "get_unit_status",
    "is_localhost",
    "is_unit_installed",
    "restart",
    "start",
    "stop",
]


# Re-export the underlying _sudo_cmd so test fixtures can patch a single
# location if they want to assert on the eventual systemctl invocation.
# Not part of the public API — internal use only.
_sudo_cmd = _sudo_cmd  # noqa: PLW0127 — explicit re-export
