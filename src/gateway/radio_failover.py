"""
Dual-Radio Failover State Machine for MeshForge Gateway.

Monitors two meshtasticd instances and automatically switches the active
transmitter when channel utilization exceeds safe thresholds. Designed to
address Meshtastic's firmware behavior of skipping position/telemetry
sends at >25% channel utilization.

Architecture:
    Primary radio  (port 4403) ─┐
                                ├─> FailoverManager ─> active_port()
    Secondary radio (port 4404) ─┘

States:
    PRIMARY_ACTIVE    → Normal operation, primary radio is the TX path
    FAILOVER_PENDING  → Primary overloaded, evaluating secondary
    SECONDARY_ACTIVE  → Secondary is the active TX path
    RECOVERY_PENDING  → Primary recovered, stabilizing before switchback

Thresholds:
    - Meshtastic firmware skips sends at >25% channel utilization
    - Pure ALOHA theoretical max is ~18.4% before collision dominance
    - SENSOR/TRACKER roles bypass the 25% throttle

Requires:
    - Two meshtasticd instances on ports 4403 and 4404
    - HTTP API enabled on both (Webserver.Port in config.yaml)

Usage:
    from gateway.radio_failover import FailoverManager, FailoverConfig

    config = FailoverConfig(enabled=True)
    manager = FailoverManager(config)
    manager.start()

    # Get current active port for TX
    port = manager.active_port  # 4403 or 4404

    # Check state
    print(manager.state)        # FailoverState.PRIMARY_ACTIVE
    print(manager.get_status()) # Dict with full status
"""

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from utils.ports import MESHTASTICD_PORT, MESHTASTICD_ALT_PORT, MESHTASTICD_WEB_PORT

# HTTP client for polling radio health (non-blocking, no TCP lock)
try:
    from utils.meshtastic_http import get_http_client, DeviceReport
    _HAS_HTTP = True
except ImportError:
    _HAS_HTTP = False

logger = logging.getLogger(__name__)


class FailoverState(Enum):
    """Dual-radio failover states."""
    PRIMARY_ACTIVE = "primary_active"
    FAILOVER_PENDING = "failover_pending"
    SECONDARY_ACTIVE = "secondary_active"
    RECOVERY_PENDING = "recovery_pending"
    DISABLED = "disabled"


@dataclass
class FailoverConfig:
    """Configuration for dual-radio failover behavior."""
    enabled: bool = False

    # Radio ports (meshtasticd TCP API)
    primary_port: int = MESHTASTICD_PORT      # 4403
    secondary_port: int = MESHTASTICD_ALT_PORT  # 4404

    # HTTP ports for health polling (meshtasticd web server)
    primary_http_port: int = MESHTASTICD_WEB_PORT  # 9443
    secondary_http_port: int = 9444                # Alt web port

    # Failover thresholds
    utilization_threshold: float = 25.0   # % channel utilization trigger
    utilization_duration: int = 30        # Seconds sustained above threshold
    tx_utilization_threshold: float = 20.0  # % TX airtime trigger

    # Recovery thresholds
    recovery_threshold: float = 15.0   # % utilization for switchback
    recovery_duration: int = 60        # Seconds stable below threshold

    # Health polling
    health_poll_interval: float = 5.0  # Seconds between health checks
    http_timeout: float = 3.0          # Seconds for HTTP request timeout

    # Safety
    max_failovers_per_hour: int = 6    # Prevent flapping
    cooldown_after_failover: int = 30  # Minimum seconds between state changes


@dataclass
class RadioHealth:
    """Health snapshot for a single radio."""
    port: int = 0
    http_port: int = 0
    reachable: bool = False
    channel_utilization: float = 0.0
    tx_utilization: float = 0.0
    battery_percent: int = 0
    has_battery: bool = False
    seconds_since_boot: int = 0
    last_check: float = 0.0
    consecutive_failures: int = 0

    @property
    def is_overloaded(self) -> bool:
        """Check if utilization exceeds safe operating threshold."""
        return self.channel_utilization >= 25.0

    @property
    def is_healthy(self) -> bool:
        """Check if radio is reachable and below recovery threshold."""
        return self.reachable and self.channel_utilization < 15.0


@dataclass
class FailoverEvent:
    """Record of a failover state transition."""
    timestamp: datetime
    from_state: FailoverState
    to_state: FailoverState
    reason: str
    primary_utilization: float
    secondary_utilization: float


class FailoverManager:
    """
    Monitors two meshtasticd instances and manages automatic failover.

    Polls both radios via HTTP /json/report (non-blocking, no TCP lock)
    and transitions between states based on channel utilization thresholds.
    """

    def __init__(
        self,
        config: Optional[FailoverConfig] = None,
        on_state_change: Optional[Callable[[FailoverState, FailoverState, str], None]] = None,
    ):
        self._config = config or FailoverConfig()
        self._on_state_change = on_state_change

        # State
        self._state = FailoverState.DISABLED if not self._config.enabled else FailoverState.PRIMARY_ACTIVE
        self._state_lock = threading.Lock()

        # Health tracking
        self._primary = RadioHealth(
            port=self._config.primary_port,
            http_port=self._config.primary_http_port,
        )
        self._secondary = RadioHealth(
            port=self._config.secondary_port,
            http_port=self._config.secondary_http_port,
        )

        # Timing
        self._overload_start: Optional[float] = None  # When primary exceeded threshold
        self._recovery_start: Optional[float] = None   # When primary dropped below threshold
        self._last_state_change: float = 0.0
        self._failover_count_window: List[float] = []  # Timestamps of recent failovers

        # Event history
        self._events: List[FailoverEvent] = []
        self._max_events = 100

        # Polling thread
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def state(self) -> FailoverState:
        """Current failover state."""
        with self._state_lock:
            return self._state

    @property
    def active_port(self) -> int:
        """TCP API port of the currently active radio for TX."""
        with self._state_lock:
            if self._state in (FailoverState.SECONDARY_ACTIVE, FailoverState.RECOVERY_PENDING):
                return self._config.secondary_port
            return self._config.primary_port

    @property
    def primary_health(self) -> RadioHealth:
        """Health snapshot of the primary radio."""
        return self._primary

    @property
    def secondary_health(self) -> RadioHealth:
        """Health snapshot of the secondary radio."""
        return self._secondary

    @property
    def events(self) -> List[FailoverEvent]:
        """Recent failover events (newest first)."""
        return list(reversed(self._events))

    def start(self) -> None:
        """Start the health polling loop in a background thread."""
        if not self._config.enabled:
            logger.info("Radio failover disabled by configuration")
            return

        if not _HAS_HTTP:
            logger.warning("Radio failover requires meshtastic_http module — disabled")
            self._state = FailoverState.DISABLED
            return

        if self._thread and self._thread.is_alive():
            logger.warning("Failover manager already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="radio-failover",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Radio failover started: primary=%d, secondary=%d, threshold=%.0f%%",
            self._config.primary_port, self._config.secondary_port,
            self._config.utilization_threshold,
        )

    def stop(self) -> None:
        """Stop the health polling loop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info("Radio failover stopped")

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive failover status for TUI dashboard."""
        with self._state_lock:
            state = self._state

        return {
            'state': state.value,
            'active_port': self.active_port,
            'enabled': self._config.enabled,
            'primary': {
                'port': self._primary.port,
                'reachable': self._primary.reachable,
                'channel_utilization': round(self._primary.channel_utilization, 1),
                'tx_utilization': round(self._primary.tx_utilization, 1),
                'overloaded': self._primary.is_overloaded,
            },
            'secondary': {
                'port': self._secondary.port,
                'reachable': self._secondary.reachable,
                'channel_utilization': round(self._secondary.channel_utilization, 1),
                'tx_utilization': round(self._secondary.tx_utilization, 1),
                'overloaded': self._secondary.is_overloaded,
            },
            'last_event': self._events[-1].reason if self._events else None,
            'failover_count_1h': len(self._failover_count_window),
            'thresholds': {
                'utilization': self._config.utilization_threshold,
                'recovery': self._config.recovery_threshold,
                'duration': self._config.utilization_duration,
            },
        }

    # ── Internal polling loop ──────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Background loop: poll both radios and evaluate state transitions."""
        while not self._stop_event.is_set():
            try:
                self._poll_radio_health(self._primary)
                self._poll_radio_health(self._secondary)
                self._evaluate_state()
            except Exception as e:
                logger.error("Failover poll error: %s", e)

            self._stop_event.wait(timeout=self._config.health_poll_interval)

    def _poll_radio_health(self, radio: RadioHealth) -> None:
        """Poll a single radio's health via HTTP /json/report."""
        try:
            client = get_http_client(
                host='localhost',
                port=radio.http_port,
                auto_detect=False,
            )
            report = client.get_device_report()
            if report:
                radio.channel_utilization = report.channel_utilization
                radio.tx_utilization = report.tx_utilization
                radio.battery_percent = report.battery_percent
                radio.has_battery = report.has_battery
                radio.seconds_since_boot = report.seconds_since_boot
                radio.reachable = True
                radio.consecutive_failures = 0
                radio.last_check = time.time()
            else:
                radio.consecutive_failures += 1
                if radio.consecutive_failures >= 3:
                    radio.reachable = False
        except Exception:
            radio.consecutive_failures += 1
            if radio.consecutive_failures >= 3:
                radio.reachable = False

    def _evaluate_state(self) -> None:
        """Evaluate whether a state transition should occur."""
        with self._state_lock:
            current = self._state

        if current == FailoverState.DISABLED:
            return

        now = time.time()

        # Cooldown check — prevent flapping
        if now - self._last_state_change < self._config.cooldown_after_failover:
            return

        # Rate limit — max failovers per hour
        self._failover_count_window = [
            t for t in self._failover_count_window if now - t < 3600
        ]

        if current == FailoverState.PRIMARY_ACTIVE:
            self._evaluate_primary_active(now)
        elif current == FailoverState.FAILOVER_PENDING:
            self._evaluate_failover_pending(now)
        elif current == FailoverState.SECONDARY_ACTIVE:
            self._evaluate_secondary_active(now)
        elif current == FailoverState.RECOVERY_PENDING:
            self._evaluate_recovery_pending(now)

    def _evaluate_primary_active(self, now: float) -> None:
        """Check if primary needs failover."""
        if not self._primary.reachable:
            # Primary unreachable — immediate failover if secondary is up
            if self._secondary.reachable:
                self._transition(
                    FailoverState.SECONDARY_ACTIVE,
                    "Primary radio unreachable, secondary available"
                )
            return

        if self._primary.channel_utilization >= self._config.utilization_threshold:
            if self._overload_start is None:
                self._overload_start = now
                logger.info(
                    "Primary radio utilization %.1f%% exceeds %.0f%% threshold — monitoring",
                    self._primary.channel_utilization, self._config.utilization_threshold,
                )
            elif now - self._overload_start >= self._config.utilization_duration:
                self._transition(
                    FailoverState.FAILOVER_PENDING,
                    f"Primary sustained >{self._config.utilization_threshold}% "
                    f"for {self._config.utilization_duration}s"
                )
                self._overload_start = None
        else:
            self._overload_start = None

    def _evaluate_failover_pending(self, now: float) -> None:
        """Evaluate if secondary is ready to take over."""
        if not self._secondary.reachable:
            logger.warning("Secondary radio unreachable — staying on primary")
            self._transition(
                FailoverState.PRIMARY_ACTIVE,
                "Secondary unreachable, reverting to primary"
            )
            return

        if self._secondary.is_overloaded:
            logger.warning(
                "Secondary also overloaded (%.1f%%) — staying on primary",
                self._secondary.channel_utilization
            )
            self._transition(
                FailoverState.PRIMARY_ACTIVE,
                "Both radios overloaded, staying on primary"
            )
            return

        # Rate limit check
        if len(self._failover_count_window) >= self._config.max_failovers_per_hour:
            logger.warning(
                "Max failovers/hour (%d) reached — staying on primary",
                self._config.max_failovers_per_hour
            )
            self._transition(
                FailoverState.PRIMARY_ACTIVE,
                "Failover rate limit reached"
            )
            return

        # Secondary looks good — activate it
        self._failover_count_window.append(now)
        self._transition(
            FailoverState.SECONDARY_ACTIVE,
            f"Failover: primary at {self._primary.channel_utilization:.1f}%, "
            f"secondary at {self._secondary.channel_utilization:.1f}%"
        )

    def _evaluate_secondary_active(self, now: float) -> None:
        """Check if primary has recovered enough to switch back."""
        if not self._primary.reachable:
            self._recovery_start = None
            return

        if self._primary.channel_utilization < self._config.recovery_threshold:
            if self._recovery_start is None:
                self._recovery_start = now
                logger.info(
                    "Primary utilization %.1f%% below recovery threshold %.0f%% — monitoring",
                    self._primary.channel_utilization, self._config.recovery_threshold,
                )
            elif now - self._recovery_start >= self._config.recovery_duration:
                self._transition(
                    FailoverState.RECOVERY_PENDING,
                    f"Primary below {self._config.recovery_threshold}% "
                    f"for {self._config.recovery_duration}s"
                )
                self._recovery_start = None
        else:
            self._recovery_start = None

    def _evaluate_recovery_pending(self, now: float) -> None:
        """Confirm primary is stable before switching back."""
        if not self._primary.reachable or self._primary.is_overloaded:
            self._transition(
                FailoverState.SECONDARY_ACTIVE,
                "Primary not stable during recovery — staying on secondary"
            )
            return

        # Primary confirmed stable
        self._transition(
            FailoverState.PRIMARY_ACTIVE,
            f"Recovery complete: primary at {self._primary.channel_utilization:.1f}%"
        )

    def _transition(self, new_state: FailoverState, reason: str) -> None:
        """Execute a state transition with logging and event recording."""
        with self._state_lock:
            old_state = self._state
            if old_state == new_state:
                return
            self._state = new_state

        self._last_state_change = time.time()

        event = FailoverEvent(
            timestamp=datetime.now(),
            from_state=old_state,
            to_state=new_state,
            reason=reason,
            primary_utilization=self._primary.channel_utilization,
            secondary_utilization=self._secondary.channel_utilization,
        )
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

        logger.warning(
            "FAILOVER: %s -> %s | %s | primary=%.1f%% secondary=%.1f%%",
            old_state.value, new_state.value, reason,
            self._primary.channel_utilization, self._secondary.channel_utilization,
        )

        if self._on_state_change:
            try:
                self._on_state_change(old_state, new_state, reason)
            except Exception as e:
                logger.error("Failover callback error: %s", e)


# ── TX Load Balancer ────────────────────────────────────────────────────
#
# Replaces binary failover with weighted TX distribution across two radios.
# Triggers on tx_utilization (our TX contribution), NOT channel_utilization
# (identical on same-channel radios).
#
# Model:
#   Primary TX < threshold  → 100% primary (IDLE)
#   Primary TX >= threshold → split across both radios (BALANCING)
#   Both radios high TX     → hold weights, warn (SATURATED)


class LoadBalancerState(Enum):
    """TX load balancer states."""
    IDLE = "idle"                # Primary handles 100% TX
    BALANCING = "balancing"      # Weights split between radios
    SATURATED = "saturated"      # Both radios high TX — warning
    DISABLED = "disabled"


@dataclass
class LoadBalancerConfig:
    """Configuration for dual-radio TX load balancing."""
    enabled: bool = False

    # Radio ports (meshtasticd TCP API)
    primary_port: int = MESHTASTICD_PORT        # 4403
    secondary_port: int = MESHTASTICD_ALT_PORT  # 4404

    # HTTP ports for health polling (meshtasticd web server)
    primary_http_port: int = MESHTASTICD_WEB_PORT  # 9443
    secondary_http_port: int = 9444

    # TX thresholds (based on tx_utilization, NOT channel_utilization)
    tx_threshold: float = 10.0   # % TX airtime to start splitting
    tx_max: float = 20.0         # % TX airtime for maximum offload

    # Polling
    health_poll_interval: float = 5.0  # Seconds between health checks
    http_timeout: float = 3.0          # Seconds for HTTP request timeout

    # Safety
    weight_change_rate: float = 10.0   # Max weight shift per poll cycle (%)
    min_primary_weight: float = 10.0   # Primary always keeps at least this %


class RadioLoadBalancer:
    """
    Weighted TX load balancer for dual-radio MeshForge gateways.

    Distributes outbound gateway traffic across two meshtasticd instances
    based on each radio's tx_utilization (its own TX airtime contribution).

    Unlike FailoverManager which uses channel_utilization (identical for
    same-channel radios), this uses tx_utilization — the metric that
    actually differs between two radios sharing a channel.

    Usage:
        config = LoadBalancerConfig(enabled=True)
        lb = RadioLoadBalancer(config)
        lb.start()

        # In TX path — call per message:
        http_port = lb.get_tx_port()  # 9443 or 9444
        send_text_direct(text=msg, host="localhost", port=http_port)
    """

    def __init__(
        self,
        config: Optional[LoadBalancerConfig] = None,
        on_state_change: Optional[Callable[[LoadBalancerState, LoadBalancerState, str], None]] = None,
        congested_node_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ):
        self._config = config or LoadBalancerConfig()
        self._on_state_change = on_state_change
        self._congested_node_provider = congested_node_provider

        # State
        self._state = LoadBalancerState.DISABLED if not self._config.enabled else LoadBalancerState.IDLE
        self._state_lock = threading.Lock()

        # Weights (primary_weight + secondary_weight = 100)
        self._primary_weight: float = 100.0
        self._weight_lock = threading.Lock()

        # Health tracking (reuses RadioHealth from failover)
        self._primary = RadioHealth(
            port=self._config.primary_port,
            http_port=self._config.primary_http_port,
        )
        self._secondary = RadioHealth(
            port=self._config.secondary_port,
            http_port=self._config.secondary_http_port,
        )

        # TX counters (per poll cycle, for status display)
        self._tx_count_primary: int = 0
        self._tx_count_secondary: int = 0

        # Event history
        self._events: List[FailoverEvent] = []
        self._max_events = 100
        self._last_state_change: float = 0.0

        # Polling thread
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def state(self) -> LoadBalancerState:
        """Current load balancer state."""
        with self._state_lock:
            return self._state

    @property
    def primary_weight(self) -> float:
        """Current primary TX weight (0-100)."""
        with self._weight_lock:
            return self._primary_weight

    @property
    def secondary_weight(self) -> float:
        """Current secondary TX weight (0-100)."""
        with self._weight_lock:
            return 100.0 - self._primary_weight

    @property
    def primary_health(self) -> RadioHealth:
        """Health snapshot of the primary radio."""
        return self._primary

    @property
    def secondary_health(self) -> RadioHealth:
        """Health snapshot of the secondary radio."""
        return self._secondary

    def get_tx_port(self) -> int:
        """Return HTTP port for next TX message based on current weights.

        Called per outbound message. Uses weighted random selection so that
        over many calls, the distribution matches the current weight ratio.

        Returns:
            HTTP port (e.g. 9443 or 9444) for the selected radio.
        """
        with self._state_lock:
            if self._state == LoadBalancerState.DISABLED:
                return self._config.primary_http_port

        with self._weight_lock:
            weight = self._primary_weight

        if random.random() * 100.0 < weight:
            self._tx_count_primary += 1
            return self._config.primary_http_port
        else:
            self._tx_count_secondary += 1
            return self._config.secondary_http_port

    def start(self) -> None:
        """Start the health polling loop in a background thread."""
        if not self._config.enabled:
            logger.info("TX load balancer disabled by configuration")
            return

        if not _HAS_HTTP:
            logger.warning("TX load balancer requires meshtastic_http module — disabled")
            self._state = LoadBalancerState.DISABLED
            return

        if self._thread and self._thread.is_alive():
            logger.warning("TX load balancer already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="radio-load-balancer",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "TX load balancer started: primary=%d, secondary=%d, "
            "tx_threshold=%.0f%%, tx_max=%.0f%%",
            self._config.primary_port, self._config.secondary_port,
            self._config.tx_threshold, self._config.tx_max,
        )

    def stop(self) -> None:
        """Stop the health polling loop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info("TX load balancer stopped")

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive load balancer status for TUI dashboard."""
        with self._state_lock:
            state = self._state
        with self._weight_lock:
            p_weight = self._primary_weight

        status = {
            'state': state.value,
            'enabled': self._config.enabled,
            'primary_weight': round(p_weight, 1),
            'secondary_weight': round(100.0 - p_weight, 1),
            'primary': {
                'port': self._primary.port,
                'http_port': self._primary.http_port,
                'reachable': self._primary.reachable,
                'channel_utilization': round(self._primary.channel_utilization, 1),
                'tx_utilization': round(self._primary.tx_utilization, 1),
            },
            'secondary': {
                'port': self._secondary.port,
                'http_port': self._secondary.http_port,
                'reachable': self._secondary.reachable,
                'channel_utilization': round(self._secondary.channel_utilization, 1),
                'tx_utilization': round(self._secondary.tx_utilization, 1),
            },
            'tx_counts': {
                'primary': self._tx_count_primary,
                'secondary': self._tx_count_secondary,
            },
            'last_event': self._events[-1].reason if self._events else None,
            'thresholds': {
                'tx_threshold': self._config.tx_threshold,
                'tx_max': self._config.tx_max,
            },
        }

        # Congested node identification
        if self._congested_node_provider:
            try:
                status['congested_nodes'] = self._congested_node_provider()
            except Exception as e:
                logger.debug("Failed to get congested nodes: %s", e)
                status['congested_nodes'] = []
        else:
            status['congested_nodes'] = []

        return status

    # ── Internal polling loop ──────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Background loop: poll both radios and recalculate TX weights."""
        while not self._stop_event.is_set():
            try:
                self._poll_radio_health(self._primary)
                self._poll_radio_health(self._secondary)
                self._recalculate_weights()
            except Exception as e:
                logger.error("Load balancer poll error: %s", e)

            self._stop_event.wait(timeout=self._config.health_poll_interval)

    def _poll_radio_health(self, radio: RadioHealth) -> None:
        """Poll a single radio's health via HTTP /json/report."""
        try:
            client = get_http_client(
                host='localhost',
                port=radio.http_port,
                auto_detect=False,
            )
            report = client.get_device_report()
            if report:
                radio.channel_utilization = report.channel_utilization
                radio.tx_utilization = report.tx_utilization
                radio.battery_percent = report.battery_percent
                radio.has_battery = report.has_battery
                radio.seconds_since_boot = report.seconds_since_boot
                radio.reachable = True
                radio.consecutive_failures = 0
                radio.last_check = time.time()
            else:
                radio.consecutive_failures += 1
                if radio.consecutive_failures >= 3:
                    radio.reachable = False
        except Exception:
            radio.consecutive_failures += 1
            if radio.consecutive_failures >= 3:
                radio.reachable = False

    def _recalculate_weights(self) -> None:
        """Recalculate TX weights based on both radios' tx_utilization."""
        # If secondary is unreachable, all traffic goes to primary
        if not self._secondary.reachable:
            self._set_weights(100.0, "Secondary unreachable")
            self._set_state(LoadBalancerState.IDLE)
            return

        # If primary is unreachable, all traffic goes to secondary
        if not self._primary.reachable:
            self._set_weights(self._config.min_primary_weight, "Primary unreachable")
            self._set_state(LoadBalancerState.BALANCING)
            return

        p_tx = self._primary.tx_utilization
        s_tx = self._secondary.tx_utilization
        threshold = self._config.tx_threshold
        tx_max = self._config.tx_max

        # Both radios high TX — saturated
        if p_tx >= tx_max and s_tx >= tx_max:
            self._set_state(LoadBalancerState.SATURATED)
            # Hold current weights, just warn
            logger.warning(
                "TX SATURATED: primary=%.1f%% secondary=%.1f%% — "
                "both radios at capacity",
                p_tx, s_tx,
            )
            return

        # Primary below threshold — all traffic to primary
        if p_tx < threshold:
            target = 100.0
            new_state = LoadBalancerState.IDLE
        else:
            # Scale: at tx_threshold → 100/0, at tx_max → min_primary/rest
            range_size = tx_max - threshold
            if range_size <= 0:
                ratio = 1.0
            else:
                ratio = min(1.0, (p_tx - threshold) / range_size)

            min_w = self._config.min_primary_weight
            target = max(min_w, 100.0 - (ratio * (100.0 - min_w)))
            new_state = LoadBalancerState.BALANCING

        # Gradual adjustment — move toward target by weight_change_rate
        with self._weight_lock:
            current = self._primary_weight

        rate = self._config.weight_change_rate
        if current > target:
            new_weight = max(target, current - rate)
        elif current < target:
            new_weight = min(target, current + rate)
        else:
            new_weight = current

        self._set_weights(new_weight, f"p_tx={p_tx:.1f}% s_tx={s_tx:.1f}%")
        self._set_state(new_state)

    def _set_weights(self, primary_weight: float, reason: str) -> None:
        """Update TX weights with logging."""
        with self._weight_lock:
            old = self._primary_weight
            self._primary_weight = max(0.0, min(100.0, primary_weight))

        if abs(old - primary_weight) > 0.5:
            logger.info(
                "TX weights: primary=%.0f%% secondary=%.0f%% (%s)",
                primary_weight, 100.0 - primary_weight, reason,
            )

    def _set_state(self, new_state: LoadBalancerState) -> None:
        """Transition to a new state if different from current."""
        with self._state_lock:
            old_state = self._state
            if old_state == new_state:
                return
            self._state = new_state

        self._last_state_change = time.time()

        with self._weight_lock:
            p_weight = self._primary_weight

        event = FailoverEvent(
            timestamp=datetime.now(),
            from_state=old_state,  # type: ignore[arg-type]
            to_state=new_state,  # type: ignore[arg-type]
            reason=f"weights={p_weight:.0f}/{100-p_weight:.0f}",
            primary_utilization=self._primary.tx_utilization,
            secondary_utilization=self._secondary.tx_utilization,
        )
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

        logger.warning(
            "LOAD BALANCER: %s -> %s | primary_tx=%.1f%% secondary_tx=%.1f%% | "
            "weights=%.0f/%.0f",
            old_state.value, new_state.value,
            self._primary.tx_utilization, self._secondary.tx_utilization,
            p_weight, 100.0 - p_weight,
        )

        if self._on_state_change:
            try:
                self._on_state_change(old_state, new_state,
                                      f"weights={p_weight:.0f}/{100-p_weight:.0f}")
            except Exception as e:
                logger.error("Load balancer callback error: %s", e)
