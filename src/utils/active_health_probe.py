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
from typing import Callable, Dict, Optional, List, Tuple
from enum import Enum

from utils.event_bus import emit_service_status
from utils.service_check import check_udp_port, check_rns_shared_instance
from utils.user_units import enabled_user_timers
from utils import tx_guard
# systemd unit resolution + /proc fd accounting — split to their own module
# 2026-08-12 (MF025 headroom for the tri-state MainPID port). Re-exported into
# this namespace on purpose: the checks below reference them as module globals,
# so `patch.object(active_health_probe, "_read_fd_usage", ...)` keeps working.
from utils.active_health_probe_core import (  # noqa: F401
    HealthState,
    HealthResult,
    ServiceHealthState,
    _LIMITS_NOFILE_RE,
    _read_fd_usage,
    _resolve_main_pid_status,
    # rnstatus-timeout confirmation (2026-09-08). Lives in _core so this
    # file stays under the MF025 cap; re-exported here because
    # check_rns_rpc_responsive and its tests are the only consumers.
    _DEFAULT_RPC_CONFIRM_TICKS,
    _RPC_CONFIRM_TICKS_ENV,
    _cpu_pressure_context,
    _rpc_confirm_ticks,
    judge_rns_rpc_timeout,
    reset_rnstatus_baseline,
    reset_rns_rpc_timeout_streak,
)
import utils.active_health_probe_core as _ahp_core
import utils.active_health_checks_delivery as _checks_delivery
import utils.active_health_checks_host as _checks_host

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
        with tx_guard.probe_connect(), \
                socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# _USER_TIMER_UNIT_RE removed 2026-08-12 with the local _enabled_user_timers —
# the Unit= parse now lives once, in utils.user_units (matches MeshForge).






# Ported from MeshForge 2026-08-12 (MF landed it 2026-08-09 in
# watchdog_probes_user; this local copy was the un-ported twin).
#
# ⚠️ The old body here read ONLY ``timers.target.wants``. Enablement is a
# symlink under **ANY** ``*.target.wants``, so a timer enabled into e.g.
# ``default.target.wants`` was invisible to this probe — its triggered service
# could have failed on every firing forever, unwatched. That is not
# hypothetical on this app: **meshanchor-server has
# ``meshanchor-map-restart.timer`` in ``default.target.wants``** (it declares
# ``WantedBy=timers.target`` but is linked from the other dir), so the box this
# app primarily runs on had a real timer outside the watch-list.
#
# ``utils.user_units.enabled_user_timers`` is byte-identical to MeshForge's and
# reads every wants dir. Two consumers, ONE definition (honest_failure_modes
# #5) — a local re-implementation is exactly how the twins drift apart.
_enabled_user_timers = enabled_user_timers


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
                # None (not 0.0) when never checked — 0.0% uptime is a fabricated
                # value for "no data" (the property keeps 0.0 for the Prometheus
                # gauge; this surface field is the honest one). (S8 L3)
                "uptime_percent": round(state.uptime_percent, 1) if state.total_checks else None,
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

    # Critical pip deps floor-checked against requirements/core.txt. rns/lxmf
    # are deliberately EXCLUDED — they live under the MF-fork pin regime
    # (requirements/rns.txt), not the core floor. The gap this fills:
    # meshtastic, the lib whose silent drift left meshanchor-server on 2.7.8
    # while the fleet pin was 2.7.9 (found by the 2026-07-03 audit — the one
    # box no MeshForge probe watches).
    DEP_FLOOR_WATCHED = ("meshtastic",)

    # ── Checks, split out 2026-09-10 (MF025) ─────────────────────────────
    # The bodies live in utils/active_health_checks_{delivery,host}.py; 9 of
    # 11 checks never touched `self`, so they are free functions there and are
    # re-exposed here. staticmethod keeps `probe.check_x(...)`, every
    # register_check lambda, and inspect.signature() behaving exactly as
    # before the move — this split changes no behaviour.
    check_delivery_confirmation_stall = staticmethod(
        _checks_delivery.check_delivery_confirmation_stall)
    check_fd_exhaustion = staticmethod(_checks_host.check_fd_exhaustion)
    check_user_timer_unit_failing = staticmethod(
        _checks_host.check_user_timer_unit_failing)

    # The two that legitimately need the instance: they supply state the free
    # function judges, rather than reaching for it themselves.
    def check_queue_backlog(self, *args, **kwargs) -> HealthResult:
        """Owns the depth-sample window; the judging lives in the module."""
        return _checks_delivery.check_queue_backlog(
            self._dl_samples, *args, **kwargs)

    def check_dep_version_floor(self, *args, **kwargs) -> HealthResult:
        """Supplies the class-level watch list to the module function."""
        return _checks_host.check_dep_version_floor(
            self.DEP_FLOOR_WATCHED, *args, **kwargs)


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
        own rnsd-down). Only a subprocess TIMEOUT (``timed_out=True``) is a
        candidate, so RNS-less boxes never false-alarm.

        A busy box is not a wedge (2026-09-08): ``rnstatus`` wall time also
        measures CPU/IO headroom, so we require ``_rpc_confirm_ticks()``
        CONSECUTIVE timeouts before reporting unhealthy; short of that the
        result is healthy with an explicitly UNCONFIRMED reason. Measured
        incident + rationale: ``active_health_probe_core``.
        """
        try:
            from utils.rns_status_parser import run_rnstatus
            status = run_rnstatus(timeout_s=timeout_s)
        except Exception as e:  # pragma: no cover - defensive
            return HealthResult(healthy=False, reason=f"rpc_check_error: {e}"[:120])
        healthy, reason = _ahp_core.judge_rns_rpc_timeout(
            status.timed_out, status.duration_s)
        return HealthResult(healthy=healthy, reason=reason)

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
    # Registered unconditionally — each check self-guards healthy when its
    # service isn't running, so boxes that don't run a given unit never
    # false-alarm. Since 2026-08-12 that guard names WHICH case it hit:
    # absent_no_unit / inactive_check_systemd_service_owns /
    # unit_state_unobservable — the last of which is a blindness, not a box
    # that simply lacks the unit.
    probe.register_check(
        "meshanchor_map_fds",
        lambda: probe.check_fd_exhaustion("meshanchor-map.service"),
    )
    probe.register_check(
        "meshanchor_daemon_fds",
        lambda: probe.check_fd_exhaustion("meshanchor-daemon.service"),
    )

    # Dep version-floor probe (MeshForge probe_dep_version_drift parity,
    # 2026-07-03): our own interpreter's meshtastic vs the requirements/
    # core.txt fleet floor — the silent-drift class that left
    # meshanchor-server on 2.7.8 with nothing watching. Registered
    # unconditionally; self-guards indeterminate (unreadable floor /
    # package not importable here) as healthy-with-reason.
    probe.register_check(
        "dep_floor",
        lambda: probe.check_dep_version_floor(),
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

    # Timer-triggered user jobs that fail on EVERY firing (MeshForge
    # probe_user_timer_unit_failing parity port, 2026-07-19). Registered
    # UNCONDITIONALLY following the fd-exhaustion precedent — it self-guards
    # healthy on a box with no user timers enrolled. This is the one shape no
    # "is it running" check can see: a oneshot is inactive between firings by
    # design, and it never crashloops. MF's kiai incident ran a week silent.
    probe.register_check(
        "user_timer_units",
        lambda: probe.check_user_timer_unit_failing(),
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
