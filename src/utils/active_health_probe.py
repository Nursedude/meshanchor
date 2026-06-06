"""
Active Health Probes for MeshAnchor Gateway Services.

Implements proactive health checking based on NGINX active health check pattern.
Unlike passive health (triggered by operations), active probes run periodically
to detect failures before user attempts connection.

Usage:
    from utils.active_health_probe import ActiveHealthProbe, HealthResult

    probe = ActiveHealthProbe(interval=30, fails=3, passes=2)
    probe.register_check("meshtastic", probe.check_meshtastic)
    probe.register_check("rns", probe.check_rns)

    probe.start()  # Background thread

    # Get current health state
    if probe.is_healthy("meshtastic"):
        connect_to_meshtastic()

    # Get detailed status
    status = probe.get_status("meshtastic")
    print(f"State: {status['state']}, Consecutive: {status['consecutive']}")

    probe.stop()

Reference:
    NGINX active health checks:
    https://docs.nginx.com/nginx/admin-guide/load-balancer/http-health-check/
"""

import logging
import re
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional, List
from enum import Enum

from utils.event_bus import emit_service_status
from utils.service_check import check_udp_port, check_rns_shared_instance

logger = logging.getLogger(__name__)


# A routable TCPInterface display_name embeds the peer host:port, e.g.
#   "Regional RNS/192.168.86.38:4242"
# RNodeInterface / AutoInterface / the Shared Instance line carry no
# host:port and are correctly ignored — only a TCP peer can be probed
# for reachability. Used by check_rns_interface_down_peer_reachable.
_RNS_TCP_PEER_RE = re.compile(r"(?P<host>[0-9.]+):(?P<port>\d+)\s*$")


def _tcp_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    """Bounded TCP-connect reachability test to ``(host, port)``.

    Returns True when a connection establishes within ``timeout`` seconds,
    False on any ``OSError`` (refused, timed out, no route, bad address).
    Module-level so tests can monkeypatch it and do zero real network I/O.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# Soft RLIMIT_NOFILE line in /proc/<pid>/limits, e.g.:
#   Max open files            1024                 524288               files
_LIMITS_NOFILE_RE = re.compile(
    r"^Max open files\s+(\d+|unlimited)\s+(\d+|unlimited)", re.MULTILINE
)


def _resolve_main_pid(
    service_name: str, *, systemctl_path: str = "systemctl",
) -> Optional[int]:
    """``systemctl show -p MainPID <service>`` parser. Returns None on any
    failure (including an inactive service, which reports ``MainPID=0``)."""
    try:
        proc = subprocess.run(
            [systemctl_path, "show", "-p", "MainPID", "--value", service_name],
            capture_output=True, text=True, timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        pid = int(proc.stdout.strip())
    except (ValueError, TypeError):
        return None
    return pid if pid > 1 else None


def _read_fd_usage(pid: int, *, proc_root: str = "/proc"):
    """Return ``(open_fd_count, soft_limit)`` for ``pid`` or None.

    Counts ``/proc/<pid>/fd`` entries and parses the *soft* ``Max open
    files`` column from ``/proc/<pid>/limits`` — the soft limit is the one a
    process actually hits ([Errno 24]). Returns None on any read failure
    (process vanished, permission, unlimited soft limit) so an unreadable
    target never alarms. Module-level so tests can build a fake /proc tree.
    """
    import os
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


class HealthState(Enum):
    """Health state for a monitored service."""
    UNKNOWN = "unknown"    # Not yet checked
    HEALTHY = "healthy"    # Passing checks
    UNHEALTHY = "unhealthy"  # Failing checks
    RECOVERING = "recovering"  # Transitioning from unhealthy to healthy


@dataclass
class HealthResult:
    """Result of a single health check."""
    healthy: bool
    reason: str = ""
    latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def __bool__(self) -> bool:
        return self.healthy


@dataclass
class ServiceHealthState:
    """Tracks health state for a single service with hysteresis."""
    name: str
    state: HealthState = HealthState.UNKNOWN
    consecutive_passes: int = 0
    consecutive_fails: int = 0
    last_check: Optional[float] = None
    last_result: Optional[HealthResult] = None
    total_checks: int = 0
    total_passes: int = 0
    total_fails: int = 0

    @property
    def uptime_percent(self) -> float:
        """Calculate uptime percentage based on total checks."""
        if self.total_checks == 0:
            return 0.0
        return (self.total_passes / self.total_checks) * 100


class ActiveHealthProbe:
    """
    Proactive health checking for mesh services.

    Based on NGINX active health check pattern:
    - Periodic checks independent of traffic
    - Hysteresis: Multiple consecutive fails before marking unhealthy
    - Recovery: Multiple consecutive passes before marking healthy

    Attributes:
        interval: Seconds between health checks
        fails: Consecutive failures to mark service unhealthy
        passes: Consecutive passes to mark service healthy
    """

    def __init__(
        self,
        interval: int = 30,
        fails: int = 3,
        passes: int = 2,
    ):
        """
        Initialize active health probe.

        Args:
            interval: Seconds between health checks (default: 30)
            fails: Consecutive failures to mark unhealthy (default: 3)
            passes: Consecutive passes to mark healthy (default: 2)
        """
        self.interval = interval
        self.fails = fails
        self.passes = passes

        self._checks: Dict[str, Callable[[], HealthResult]] = {}
        self._states: Dict[str, ServiceHealthState] = {}
        self._callbacks: Dict[str, List[Callable[[str, HealthState], None]]] = {
            "on_healthy": [],
            "on_unhealthy": [],
            "on_state_change": [],
        }

        self._stop_event = threading.Event()  # Set to signal stop
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

        # MF Issue #74 probe port: trailing dead-letter samples for
        # check_queue_backlog's growth judgment. (ts, count) pairs;
        # restart re-baselines (first post-restart sample = baseline,
        # so a static historical dead-letter pile never false-alarms).
        self._dl_samples: deque = deque()

    def register_check(
        self,
        service_name: str,
        check_fn: Callable[[], HealthResult],
    ) -> None:
        """
        Register a health check function for a service.

        Args:
            service_name: Unique name for the service
            check_fn: Function that returns HealthResult
        """
        with self._lock:
            self._checks[service_name] = check_fn
            self._states[service_name] = ServiceHealthState(name=service_name)
            logger.debug(f"Registered health check: {service_name}")

    def register_callback(
        self,
        event: str,
        callback: Callable[[str, HealthState], None],
    ) -> None:
        """
        Register a callback for health state changes.

        Args:
            event: Event type - "on_healthy", "on_unhealthy", "on_state_change"
            callback: Function(service_name, new_state)
        """
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _run_check(self, service_name: str) -> HealthResult:
        """Run health check for a service and update state."""
        check_fn = self._checks.get(service_name)
        if not check_fn:
            return HealthResult(healthy=False, reason="no_check_registered")

        start_time = time.time()
        try:
            result = check_fn()
            result.latency_ms = (time.time() - start_time) * 1000
            result.timestamp = time.time()
        except Exception as e:
            result = HealthResult(
                healthy=False,
                reason=f"check_exception: {e}",
                latency_ms=(time.time() - start_time) * 1000,
            )

        # Update state with hysteresis
        with self._lock:
            state = self._states[service_name]
            old_state = state.state

            state.last_check = result.timestamp
            state.last_result = result
            state.total_checks += 1

            if result.healthy:
                state.consecutive_passes += 1
                state.consecutive_fails = 0
                state.total_passes += 1

                # Check if we should transition to healthy
                if state.state != HealthState.HEALTHY:
                    if state.consecutive_passes >= self.passes:
                        state.state = HealthState.HEALTHY
                        state.consecutive_passes = 0
                        logger.info(f"Health probe: {service_name} is now HEALTHY")
                    elif state.state == HealthState.UNHEALTHY:
                        state.state = HealthState.RECOVERING
                        logger.debug(
                            f"Health probe: {service_name} recovering "
                            f"({state.consecutive_passes}/{self.passes})"
                        )
            else:
                state.consecutive_fails += 1
                state.consecutive_passes = 0
                state.total_fails += 1

                # Check if we should transition to unhealthy
                if state.state != HealthState.UNHEALTHY:
                    if state.consecutive_fails >= self.fails:
                        state.state = HealthState.UNHEALTHY
                        state.consecutive_fails = 0
                        logger.warning(
                            f"Health probe: {service_name} is now UNHEALTHY: "
                            f"{result.reason}"
                        )
                    elif state.state == HealthState.RECOVERING:
                        # Reset to unhealthy if we fail during recovery
                        state.state = HealthState.UNHEALTHY
                        logger.debug(
                            f"Health probe: {service_name} recovery failed"
                        )

            # Fire callbacks on state change
            new_state = state.state
            if old_state != new_state:
                self._fire_callbacks(service_name, new_state)

        return result

    def _fire_callbacks(self, service_name: str, new_state: HealthState) -> None:
        """Fire registered callbacks for state change."""
        # Always fire state_change
        for callback in self._callbacks["on_state_change"]:
            try:
                callback(service_name, new_state)
            except Exception as e:
                logger.debug(f"Health callback error: {e}")

        # Fire specific event callbacks
        if new_state == HealthState.HEALTHY:
            for callback in self._callbacks["on_healthy"]:
                try:
                    callback(service_name, new_state)
                except Exception as e:
                    logger.debug(f"Health callback error: {e}")
        elif new_state == HealthState.UNHEALTHY:
            for callback in self._callbacks["on_unhealthy"]:
                try:
                    callback(service_name, new_state)
                except Exception as e:
                    logger.debug(f"Health callback error: {e}")

    def _probe_loop(self) -> None:
        """Background thread that runs periodic health checks.

        The outer try/except acts as a watchdog — if the loop body
        crashes due to an unexpected error (e.g., dict mutation during
        iteration), we log it and continue rather than letting the
        probe thread die silently.
        """
        logger.info(
            f"Active health probe started (interval={self.interval}s, "
            f"fails={self.fails}, passes={self.passes})"
        )
        loop_errors = 0

        while not self._stop_event.is_set():
            try:
                services = list(self._checks.keys())
                for service_name in services:
                    if self._stop_event.is_set():
                        break
                    try:
                        self._run_check(service_name)
                    except Exception as e:
                        logger.debug(f"Health check error for {service_name}: {e}")

                # Wait for interval or until stop is signaled
                # wait() returns True if event was set, False on timeout
                self._stop_event.wait(self.interval)
            except Exception as e:
                loop_errors += 1
                logger.warning(
                    f"Health probe loop error #{loop_errors}: {e}"
                )
                # Back off briefly to avoid tight error loops
                self._stop_event.wait(min(self.interval, 5))

        logger.info("Active health probe stopped")

    def start(self) -> None:
        """Start the background health probe thread."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._probe_loop, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background health probe thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def check_now(self, service_name: str) -> HealthResult:
        """
        Run an immediate health check (bypass interval).

        Args:
            service_name: Service to check

        Returns:
            HealthResult from the check
        """
        return self._run_check(service_name)

    def is_healthy(self, service_name: str) -> bool:
        """
        Check if a service is currently healthy.

        Args:
            service_name: Service to check

        Returns:
            True if service state is HEALTHY
        """
        with self._lock:
            state = self._states.get(service_name)
            if not state:
                return False
            return state.state == HealthState.HEALTHY

    def get_status(self, service_name: str) -> Optional[Dict]:
        """
        Get detailed health status for a service.

        Args:
            service_name: Service to get status for

        Returns:
            Dict with state info, or None if service not registered
        """
        with self._lock:
            state = self._states.get(service_name)
            if not state:
                return None

            return {
                "name": state.name,
                "state": state.state.value,
                "consecutive_passes": state.consecutive_passes,
                "consecutive_fails": state.consecutive_fails,
                "last_check": state.last_check,
                "last_result": {
                    "healthy": state.last_result.healthy,
                    "reason": state.last_result.reason,
                    "latency_ms": state.last_result.latency_ms,
                } if state.last_result else None,
                "uptime_percent": round(state.uptime_percent, 1),
                "total_checks": state.total_checks,
            }

    def get_all_status(self) -> Dict[str, Dict]:
        """Get health status for all registered services."""
        result = {}
        for service_name in self._checks:
            status = self.get_status(service_name)
            if status:
                result[service_name] = status
        return result

    # =========================================================================
    # Built-in health check functions
    # =========================================================================

    def check_meshtastic(self) -> HealthResult:
        """
        Probe meshtasticd with lightweight request.

        Uses 'meshtastic --info' as a quick connectivity test.
        This is lightweight and doesn't modify device state.
        """
        try:
            result = subprocess.run(
                ["meshtastic", "--info"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                return HealthResult(healthy=True, reason="info_success")

            # Check for specific error patterns
            stderr = result.stderr.lower()
            if "no device" in stderr or "not found" in stderr:
                return HealthResult(healthy=False, reason="no_device")
            if "connection refused" in stderr:
                return HealthResult(healthy=False, reason="connection_refused")
            if "permission denied" in stderr:
                return HealthResult(healthy=False, reason="permission_denied")

            return HealthResult(
                healthy=False,
                reason=f"exit_code_{result.returncode}",
            )

        except subprocess.TimeoutExpired:
            return HealthResult(healthy=False, reason="timeout")
        except FileNotFoundError:
            return HealthResult(healthy=False, reason="meshtastic_cli_not_found")
        except Exception as e:
            return HealthResult(healthy=False, reason=str(e)[:100])

    def check_rns_port(self, port: int = 37428, host: str = "127.0.0.1") -> HealthResult:
        """
        Probe RNS shared instance availability.

        Uses check_rns_shared_instance() which checks abstract Unix domain
        sockets (Linux default), TCP, and UDP for reliable detection.

        Args:
            port: RNS shared instance port for TCP/UDP fallback (default: 37428)
            host: Host to check (default: 127.0.0.1)
        """
        try:
            if check_rns_shared_instance(port=port):
                return HealthResult(healthy=True, reason="shared_instance_available")
            return HealthResult(healthy=False, reason="shared_instance_unavailable")
        except Exception as e:
            return HealthResult(healthy=False, reason=f"check_error: {e}")

    def check_rns_rpc_responsive(self, timeout_s: float = 8.0) -> HealthResult:
        """Detect a wedged rnsd RPC: ``rnstatus`` itself hangs even though
        the shared-instance socket accepts connections (Issue #68/#72 class).

        ``check_rns_port`` catches "no listener / connect refused". This
        catches the OPPOSITE: connect succeeds, then the RPC round-trip
        (``rpc_connection.recv`` deep in ``RNS.Reticulum``) hangs or EOFs.
        ``rnstatus`` is the canonical RPC client, so running it bounded and
        observing a TIMEOUT is the direct test for "RPC wedged".

        A genuinely down rnsd fails FAST (binary missing / no shared
        instance / refused) — that leaves ``RNSStatus.timed_out`` False and
        we report healthy here (``check_rns_port`` / ``check_systemd_service``
        own rnsd-down). Only a subprocess TIMEOUT (``timed_out=True``) is
        reported unhealthy, so RNS-less boxes never false-alarm.
        """
        try:
            from utils.rns_status_parser import run_rnstatus
            status = run_rnstatus(timeout_s=timeout_s)
        except Exception as e:  # pragma: no cover - defensive
            return HealthResult(healthy=False, reason=f"rpc_check_error: {e}"[:120])
        if status.timed_out:
            return HealthResult(
                healthy=False,
                reason=(
                    "rns_rpc_unresponsive: rnstatus timed out — rnsd accepts "
                    "shared-instance connects but the RPC round-trip is wedged. "
                    "Recovery: restart rnsd.service then RNS-using services."
                ),
            )
        return HealthResult(healthy=True, reason="rpc_responsive")

    def check_rns_interface_down_peer_reachable(
        self,
        *,
        rnstatus_status=None,
        reachable_timeout_s: float = 3.0,
    ) -> HealthResult:
        """Detect a TCPInterface stuck ``Status: Down`` while its peer
        host:port is still TCP-reachable — the 2026-05-30 islanding shape.

        Production incident: rnsd healthy (Up, owns ``@rns``, answers
        ``rnstatus``) and the peer host:port + L3 reachable, but the box's
        SOLE RNS uplink ``TCPInterface`` sat ``Status: Down`` — the box was
        islanded until rnsd was restarted.

        Logic: for each TCPInterface that is Down AND whose display name
        embeds a routable ``host:port``, run a bounded TCP-connect. If the
        peer ANSWERS → wedge (interface stuck, not a peer outage) → report
        unhealthy. If the connect FAILS → genuine peer/network outage, not
        ours; report healthy. ``rnstatus`` errored (rnsd down / wedged-RPC)
        → healthy here; ``check_rns_port`` / ``check_rns_rpc_responsive``
        own those.

        ``rnstatus_status`` lets a caller pass a pre-fetched ``RNSStatus``
        (and lets tests inject one with zero network I/O).
        """
        try:
            from utils.rns_status_parser import run_rnstatus, InterfaceStatus
            status = rnstatus_status if rnstatus_status is not None else run_rnstatus()
        except Exception as e:  # pragma: no cover - defensive
            return HealthResult(healthy=False, reason=f"iface_check_error: {e}"[:120])

        if status.parse_error:
            return HealthResult(healthy=True, reason="rnstatus_unavailable")

        for iface in status.interfaces:
            # Only TCP interfaces carry a routable peer host:port.
            if "tcp" not in iface.type_name.lower():
                continue
            if iface.status != InterfaceStatus.DOWN:
                continue
            m = _RNS_TCP_PEER_RE.search(iface.display_name)
            if not m:
                continue
            host = m.group("host")
            try:
                port = int(m.group("port"))
            except (TypeError, ValueError):
                continue
            if _tcp_reachable(host, port, timeout=reachable_timeout_s):
                return HealthResult(
                    healthy=False,
                    reason=(
                        f"rns_interface_down_peer_reachable: {iface.full_name} "
                        f"Down but {host}:{port} TCP-reachable — stuck uplink, "
                        f"box may be islanded. Recovery: restart rnsd.service."
                    ),
                )
        return HealthResult(healthy=True, reason="no_stuck_interface")

    def check_fd_exhaustion(
        self,
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
        Healthy (and quiet) when the service is inactive (``MainPID``
        unresolved — ``check_systemd_service`` owns down), /proc is
        unreadable, the soft limit is unlimited, or usage is below the
        degraded threshold — a healthy process must not false-alarm.
        """
        pid = main_pid if main_pid is not None else _resolve_main_pid(
            service_name, systemctl_path=systemctl_path
        )
        if pid is None:
            return HealthResult(healthy=True, reason="inactive_or_unresolved")

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

    def check_queue_backlog(
        self,
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

        self._dl_samples.append((ts_now, dead))
        while self._dl_samples and ts_now - self._dl_samples[0][0] > growth_window_s:
            self._dl_samples.popleft()
        baseline = self._dl_samples[0][1]
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
        self,
        *,
        min_sent: int = 20,
        rate_degraded: float = 0.50,
        rate_wedge: float = 0.10,
        snap: Optional[dict] = None,
    ) -> HealthResult:
        """Sends flow but confirmations collapsed (MF Issue #74 probe port).

        Closes the honest-signal gap where the bridge's own rolling
        metrics decay to quiet/healthy when events stop — a gateway
        shouting into a void looks fine from inside; this judges from
        the durable counters instead. Windowed rate from the
        ``delivery_counters.snapshot()`` recent-events ring (last 50)
        — the lifetime-cumulative rate would mask a recent collapse on
        a long-lived box.

        Self-guards healthy (silence is NOT failure here — the
        explicit inversion of the channel-dark class): counters
        unavailable; ``confirmation_rate is None`` (zero sends ever);
        ring SENT < ``min_sent`` (one unconfirmed message must not
        tank a tiny denominator; busy gateways canary for the fleet).

        In the daemon process this reads the writer's own snapshot; in
        the agent it reads cross-process via the SQLite file (the #74
        write-error persistence keeps that honest). ``snap`` is a test
        seam.
        """
        if snap is None:
            try:
                from gateway.delivery_counters import snapshot as _snapshot
                snap = _snapshot()
            except Exception:
                return HealthResult(
                    healthy=True, reason="delivery_counters_unavailable",
                )
        if not isinstance(snap, dict) or snap.get("confirmation_rate") is None:
            return HealthResult(healthy=True, reason="no_traffic")

        recent = snap.get("recent")
        if not isinstance(recent, list):
            return HealthResult(healthy=True, reason="no_recent_ring")

        ring_sent = sum(1 for e in recent
                        if isinstance(e, dict) and e.get("state") == "sent")
        ring_conf = sum(1 for e in recent
                        if isinstance(e, dict) and e.get("state") == "confirmed")
        if ring_sent < min_sent:
            return HealthResult(
                healthy=True,
                reason=f"low_traffic ring_sent={ring_sent}<{min_sent}",
            )

        rate = ring_conf / ring_sent
        if rate > rate_degraded:
            return HealthResult(
                healthy=True,
                reason=f"confirm_ok {ring_conf}/{ring_sent} ({rate:.0%})",
            )

        level = "wedge" if rate <= rate_wedge else "degraded"
        return HealthResult(
            healthy=False,
            reason=(
                f"delivery_confirmation_stall ({level}): {ring_conf}/"
                f"{ring_sent} confirmed in the recent-events window "
                f"({rate:.0%}) while sends keep flowing. Receivers "
                f"aren't acking — check RNS paths to the fan-out peers "
                f"and /api/gateway/delivery drop_reasons."
            ),
        )

    def check_systemd_service(self, service_name: str) -> HealthResult:
        """
        Check if a systemd service is active and running.

        Args:
            service_name: Name of the systemd service
        """
        try:
            result = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                status = result.stdout.strip()
                if status == "active":
                    return HealthResult(healthy=True, reason="active")
                return HealthResult(healthy=False, reason=f"status_{status}")

            status = result.stdout.strip()
            return HealthResult(healthy=False, reason=f"inactive_{status}")

        except subprocess.TimeoutExpired:
            return HealthResult(healthy=False, reason="timeout")
        except FileNotFoundError:
            return HealthResult(healthy=False, reason="systemctl_not_found")
        except Exception as e:
            return HealthResult(healthy=False, reason=str(e)[:100])

    def check_tcp_port(self, port: int, host: str = "localhost") -> HealthResult:
        """
        Check if a TCP port is accepting connections.

        Args:
            port: TCP port number
            host: Host to check (default: localhost)
        """
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, port))
            if result == 0:
                return HealthResult(healthy=True, reason="connected")
            return HealthResult(healthy=False, reason=f"connect_failed_{result}")
        except socket.timeout:
            return HealthResult(healthy=False, reason="timeout")
        except socket.error as e:
            return HealthResult(healthy=False, reason=f"socket_error: {e}")
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass


def _emit_state_change(service_name: str, new_state: HealthState) -> None:
    """Callback that bridges health probe state changes to the EventBus.

    Emits a ServiceEvent whenever a service transitions between states,
    enabling the status bar and other subscribers to react without polling.
    """
    available = new_state == HealthState.HEALTHY
    emit_service_status(
        service_name=service_name,
        available=available,
        message=f"{service_name}: {new_state.value}",
    )


# Module-level singleton so all callers share one probe instance
_health_probe: Optional[ActiveHealthProbe] = None
_probe_lock = threading.Lock()


def get_health_probe(
    interval: int = 30,
    fails: int = 3,
    passes: int = 2,
) -> ActiveHealthProbe:
    """
    Get the singleton health probe, creating it on first call.

    Returns the same instance to every caller so that all components
    share one background monitoring thread. The probe is NOT started
    automatically — call .start() when ready.

    Args:
        interval: Seconds between checks (only used on first call)
        fails: Consecutive failures for unhealthy (only used on first call)
        passes: Consecutive passes for healthy (only used on first call)

    Returns:
        Configured ActiveHealthProbe (call .start() to begin monitoring)
    """
    global _health_probe
    with _probe_lock:
        if _health_probe is not None:
            return _health_probe
        _health_probe = create_gateway_health_probe(
            interval=interval, fails=fails, passes=passes,
        )
        return _health_probe


def create_gateway_health_probe(
    interval: int = 30,
    fails: int = 3,
    passes: int = 2,
) -> ActiveHealthProbe:
    """
    Create a pre-configured health probe for gateway services.

    Convenience factory that sets up standard checks for:
    - meshtasticd (systemd + TCP port 4403)
    - rnsd (UDP port 37428)
    - mosquitto (TCP port 1883)

    Automatically wires state changes to the EventBus so that
    status_bar and other subscribers get push updates.

    Args:
        interval: Seconds between checks
        fails: Consecutive failures for unhealthy
        passes: Consecutive passes for healthy

    Returns:
        Configured ActiveHealthProbe ready to start()
    """
    probe = ActiveHealthProbe(interval=interval, fails=fails, passes=passes)

    # Two filters layered:
    #   1. noc.yaml's `services.<name>.managed: false` — explicit per-host
    #      opt-out (mirrors the orchestrator's _apply_managed_overrides).
    #   2. Active deployment profile — services not in the profile's
    #      required+optional sets are skipped so MESHCORE-only boxes don't
    #      flag UNHEALTHY for meshtasticd/rnsd they intentionally don't run.
    # Either filter is sufficient to skip a probe.
    from utils.profile_services import is_managed

    unmanaged = _unmanaged_services()

    def _should_probe(name: str) -> bool:
        return name not in unmanaged and is_managed(name)

    if _should_probe("meshtasticd"):
        probe.register_check(
            "meshtasticd",
            lambda: probe.check_tcp_port(4403),
        )
    if _should_probe("rnsd"):
        probe.register_check(
            "rnsd",
            lambda: probe.check_rns_port(37428),
        )
        # RNS-reliability parity port (2026-05-31): two probes for the
        # rnsd-RPC fragility class that check_rns_port can't see —
        #   rnsd_rpc:       rnstatus hangs though the socket accepts (#68/#72)
        #   rnsd_interface: TCPInterface stuck Down while peer reachable (2026-05-30)
        # Same 30s cadence; surface through the existing health-probe status.
        probe.register_check(
            "rnsd_rpc",
            lambda: probe.check_rns_rpc_responsive(),
        )
        probe.register_check(
            "rnsd_interface",
            lambda: probe.check_rns_interface_down_peer_reachable(),
        )
    if _should_probe("mosquitto"):
        probe.register_check(
            "mosquitto",
            lambda: probe.check_tcp_port(1883),
        )

    # FD-exhaustion probes (MeshForge Issue #73 parity port, 2026-05-31).
    # Proactive companion to the port wedge checks: catches a leaking fd
    # count climbing toward the soft RLIMIT_NOFILE *before* it starves
    # accept()/file-opens and wedges a service. Both long-lived processes
    # run their own mqtt_subscriber and so are fd-leak-prone:
    #   - meshanchor-map     wedged its :5000 accept() (the original incident)
    #   - meshanchor-daemon  starved SQLite opens → message_queue "unable to
    #                        open database file" (2026-05-31, same leak, 2nd
    #                        victim — surfaced after the map fix).
    # Registered unconditionally — each check self-guards, returning healthy
    # "inactive_or_unresolved" when its service isn't running, so boxes that
    # don't run a given unit never false-alarm.
    probe.register_check(
        "meshanchor_map_fds",
        lambda: probe.check_fd_exhaustion("meshanchor-map.service"),
    )
    probe.register_check(
        "meshanchor_daemon_fds",
        lambda: probe.check_fd_exhaustion("meshanchor-daemon.service"),
    )

    # MF Issue #74 probe port (delivery observability): queue
    # backpressure + delivery-confirmation stall. Registered
    # UNCONDITIONALLY following the fd-exhaustion precedent — each
    # check self-guards healthy when the queue/counters are
    # unobservable, so non-gateway boxes and the agent process never
    # false-alarm. The daemon is the counters writer (reads its own
    # snapshot); the agent reads cross-process via the SQLite file.
    probe.register_check(
        "queue_backlog",
        lambda: probe.check_queue_backlog(),
    )
    probe.register_check(
        "delivery_confirmation_stall",
        lambda: probe.check_delivery_confirmation_stall(),
    )

    # Wire state changes to EventBus for push-based status updates
    probe.register_callback("on_state_change", _emit_state_change)

    return probe


def _unmanaged_services() -> set:
    """Return service names with `managed: false` in /etc/meshanchor/noc.yaml.

    Returns empty set if noc.yaml is absent, malformed, or PyYAML is
    unavailable — preserving the all-checks-on default for unconfigured
    boxes.
    """
    try:
        import yaml as _yaml
    except ImportError:
        return set()

    config_path = Path("/etc/meshanchor/noc.yaml")
    if not config_path.exists():
        return set()

    try:
        with open(config_path) as f:
            data = _yaml.safe_load(f) or {}
    except Exception:
        return set()

    # noc.yaml nests under a top-level `noc:` key (see orchestrator
    # _load_config). install_noc.sh has emitted that shape since 2026-04-19;
    # accept both shapes so flat hand-edited configs still work.
    noc = data.get('noc', data) if isinstance(data.get('noc'), dict) else data
    services = noc.get('services', {}) or {}
    if not isinstance(services, dict):
        return set()

    return {
        name for name, cfg in services.items()
        if isinstance(cfg, dict) and cfg.get('managed', True) is False
    }
