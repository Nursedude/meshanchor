"""
Bridge Health Monitor - Tracks gateway bridge reliability metrics.

Monitors connection health, message flow, error rates, and provides
status summaries for the TUI and diagnostics system.

Usage:
    from gateway.bridge_health import BridgeHealthMonitor

    health = BridgeHealthMonitor()
    health.record_message_sent("mesh_to_rns")
    health.record_connection_event("meshtastic", "connected")
    print(health.get_summary())
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ConnectionEvent:
    """A connection state change event."""
    timestamp: float
    service: str  # "meshtastic" or "rns"
    event: str    # "connected", "disconnected", "error", "retry"
    detail: str = ""


@dataclass
class ErrorEvent:
    """A categorized error event."""
    timestamp: float
    service: str
    category: str    # "transient", "permanent", "unknown"
    message: str
    is_retriable: bool = True


# Error patterns that indicate permanent (non-retriable) failures
PERMANENT_ERROR_PATTERNS = [
    "signal only works in main thread",
    "reinitialise",
    "already running",
    "permission denied",
    "no such device",
    "module not found",
    "import error",
]

# Error patterns that indicate transient (retriable) failures
TRANSIENT_ERROR_PATTERNS = [
    "connection reset",
    "connection refused",
    "broken pipe",
    "timeout",
    "timed out",
    "temporarily unavailable",
    "network unreachable",
    "no route to host",
    "address already in use",
]


def classify_error(error: Exception) -> str:
    """Classify an error as transient or permanent.

    Args:
        error: The exception to classify.

    Returns:
        "transient", "permanent", or "unknown"
    """
    msg = str(error).lower()

    for pattern in PERMANENT_ERROR_PATTERNS:
        if pattern in msg:
            return "permanent"

    for pattern in TRANSIENT_ERROR_PATTERNS:
        if pattern in msg:
            return "transient"

    # Connection-type exceptions are generally transient
    if isinstance(error, (ConnectionError, BrokenPipeError,
                          ConnectionResetError, TimeoutError, OSError)):
        return "transient"

    return "unknown"


class BridgeHealthMonitor:
    """Monitors bridge health and collects operational metrics.

    Thread-safe. Maintains rolling windows of events for analysis
    without unbounded memory growth.
    """

    def __init__(self, window_size: int = 1000):
        """Initialize health monitor.

        Args:
            window_size: Maximum events to keep in rolling windows.
        """
        self._lock = threading.RLock()  # Reentrant: get_summary calls get_uptime_percent
        self._window_size = window_size

        # Connection state
        self._connected: Dict[str, bool] = {
            "meshtastic": False,
            "rns": False,
        }
        self._last_connected: Dict[str, float] = {}
        self._last_disconnected: Dict[str, float] = {}
        self._connection_count: Dict[str, int] = {
            "meshtastic": 0,
            "rns": 0,
        }

        # Message counters
        self._messages_sent: Dict[str, int] = {
            "mesh_to_rns": 0,
            "rns_to_mesh": 0,
        }
        self._messages_failed: Dict[str, int] = {
            "mesh_to_rns": 0,
            "rns_to_mesh": 0,
        }
        self._messages_requeued: int = 0

        # Rolling event windows
        self._connection_events: deque = deque(maxlen=window_size)
        self._error_events: deque = deque(maxlen=window_size)
        self._message_timestamps: deque = deque(maxlen=window_size)

        # Timing
        self._start_time: float = time.time()
        self._uptime_seconds: Dict[str, float] = {
            "meshtastic": 0.0,
            "rns": 0.0,
        }

    def record_connection_event(self, service: str, event: str,
                                detail: str = "") -> None:
        """Record a connection state change.

        Args:
            service: "meshtastic" or "rns"
            event: "connected", "disconnected", "error", "retry"
            detail: Optional detail message.
        """
        now = time.time()
        with self._lock:
            self._connection_events.append(ConnectionEvent(
                timestamp=now, service=service, event=event, detail=detail
            ))

            if event == "connected":
                # Track uptime from last disconnect
                if not self._connected[service]:
                    self._connected[service] = True
                    self._last_connected[service] = now
                    self._connection_count[service] += 1

            elif event in ("disconnected", "error"):
                if self._connected[service]:
                    # Accumulate uptime
                    connected_at = self._last_connected.get(service, now)
                    self._uptime_seconds[service] += now - connected_at
                self._connected[service] = False
                self._last_disconnected[service] = now

    def record_message_sent(self, direction: str) -> None:
        """Record a successfully bridged message.

        Args:
            direction: "mesh_to_rns" or "rns_to_mesh"
        """
        now = time.time()
        with self._lock:
            self._messages_sent[direction] = self._messages_sent.get(direction, 0) + 1
            self._message_timestamps.append(now)

    def record_message_failed(self, direction: str, requeued: bool = False) -> None:
        """Record a failed message send.

        Args:
            direction: "mesh_to_rns" or "rns_to_mesh"
            requeued: Whether the message was saved to persistent queue.
        """
        with self._lock:
            self._messages_failed[direction] = self._messages_failed.get(direction, 0) + 1
            if requeued:
                self._messages_requeued += 1

    def record_error(self, service: str, error: Exception) -> str:
        """Record and classify an error.

        Args:
            service: "meshtastic" or "rns"
            error: The exception that occurred.

        Returns:
            The error category ("transient", "permanent", "unknown").
        """
        category = classify_error(error)
        now = time.time()
        with self._lock:
            self._error_events.append(ErrorEvent(
                timestamp=now,
                service=service,
                category=category,
                message=str(error)[:200],
                is_retriable=(category == "transient"),
            ))
        return category

    def get_message_rate(self, window_seconds: int = 300) -> float:
        """Get messages per minute over a time window.

        Args:
            window_seconds: Time window to calculate rate over.

        Returns:
            Messages per minute.
        """
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            recent = sum(1 for t in self._message_timestamps if t >= cutoff)
        return (recent / window_seconds) * 60 if window_seconds > 0 else 0

    def get_error_rate(self, window_seconds: int = 300) -> Dict[str, int]:
        """Get error counts by category in a time window.

        Args:
            window_seconds: Time window to count errors in.

        Returns:
            Dict with transient/permanent/unknown counts.
        """
        now = time.time()
        cutoff = now - window_seconds
        counts = {"transient": 0, "permanent": 0, "unknown": 0}
        with self._lock:
            for event in self._error_events:
                if event.timestamp >= cutoff:
                    counts[event.category] = counts.get(event.category, 0) + 1
        return counts

    def get_uptime_percent(self, service: str) -> float:
        """Get connection uptime percentage for a service.

        Args:
            service: "meshtastic" or "rns"

        Returns:
            Uptime as percentage (0-100).
        """
        now = time.time()
        total_time = now - self._start_time
        if total_time <= 0:
            return 0.0

        with self._lock:
            uptime = self._uptime_seconds.get(service, 0.0)
            # Add current connected time if still connected
            if self._connected.get(service, False):
                connected_at = self._last_connected.get(service, now)
                uptime += now - connected_at

        return min(100.0, (uptime / total_time) * 100)

    def get_summary(self) -> Dict[str, Any]:
        """Get a comprehensive health summary.

        Returns:
            Dict with all health metrics suitable for API/display.
        """
        now = time.time()
        with self._lock:
            return {
                "uptime_seconds": now - self._start_time,
                "connections": {
                    "meshtastic": {
                        "connected": self._connected.get("meshtastic", False),
                        "uptime_percent": self.get_uptime_percent("meshtastic"),
                        "reconnect_count": self._connection_count.get("meshtastic", 0),
                        "last_connected": self._last_connected.get("meshtastic"),
                        "last_disconnected": self._last_disconnected.get("meshtastic"),
                    },
                    "rns": {
                        "connected": self._connected.get("rns", False),
                        "uptime_percent": self.get_uptime_percent("rns"),
                        "reconnect_count": self._connection_count.get("rns", 0),
                        "last_connected": self._last_connected.get("rns"),
                        "last_disconnected": self._last_disconnected.get("rns"),
                    },
                },
                "messages": {
                    "mesh_to_rns": self._messages_sent.get("mesh_to_rns", 0),
                    "rns_to_mesh": self._messages_sent.get("rns_to_mesh", 0),
                    "failed_mesh_to_rns": self._messages_failed.get("mesh_to_rns", 0),
                    "failed_rns_to_mesh": self._messages_failed.get("rns_to_mesh", 0),
                    "requeued": self._messages_requeued,
                    "rate_per_min": self.get_message_rate(),
                },
                "errors": self.get_error_rate(),
            }

    def is_healthy(self) -> bool:
        """Quick health check: is the bridge operational?

        Returns True if at least one connection is active and
        error rate is not excessive.
        """
        with self._lock:
            any_connected = any(self._connected.values())
        errors = self.get_error_rate(window_seconds=60)
        error_count = sum(errors.values())
        return any_connected and error_count < 10
