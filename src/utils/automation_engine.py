"""
Automation Engine - Periodic network automation tasks (MeshMonitor-inspired).

Provides configurable auto-ping, auto-traceroute, and auto-welcome features
for mesh network monitoring and node greeting.

Usage:
    from utils.automation_engine import AutomationEngine

    engine = AutomationEngine()
    engine.start()

    # Check status
    print(engine.get_status())

    # Later
    engine.stop()

Configuration persisted at ~/.config/meshforge/automation.json
"""

import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

from utils.safe_import import safe_import
from utils.common import SettingsManager

logger = logging.getLogger(__name__)

_get_node_tracker, _HAS_NODE_TRACKER = safe_import(
    'gateway.node_tracker', 'get_node_tracker'
)

# Rate limiting constants
MAX_PINGS_PER_MINUTE = 2
MAX_TRACEROUTES_PER_MINUTE = 1
MIN_REQUEST_INTERVAL_SECONDS = 5

# Default configuration
AUTOMATION_DEFAULTS = {
    "auto_ping": {
        "enabled": False,
        "interval_minutes": 15,
        "targets": [],
        "timeout_seconds": 30,
    },
    "auto_traceroute": {
        "enabled": False,
        "interval_minutes": 60,
        "targets": [],
        "timeout_seconds": 60,
    },
    "auto_welcome": {
        "enabled": False,
        "message": "Welcome to the mesh!",
        "cooldown_hours": 24,
    },
}


@dataclass
class PingResult:
    """Result from an auto-ping operation."""
    node_id: str
    timestamp: datetime
    success: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None


@dataclass
class TracerouteResult:
    """Result from an auto-traceroute operation."""
    node_id: str
    timestamp: datetime
    success: bool
    hops: int = 0
    output: str = ""
    error: Optional[str] = None


class AutomationEngine:
    """
    Periodic network automation tasks for mesh monitoring.

    Runs configurable background tasks:
    - Auto-ping: Periodically ping target nodes, track latency
    - Auto-traceroute: Periodically trace routes to target nodes
    - Auto-welcome: Send welcome message to newly discovered nodes
    """

    def __init__(self, meshtastic_host: str = "localhost"):
        """
        Initialize the automation engine.

        Args:
            meshtastic_host: Host for meshtastic CLI --host flag
        """
        self._settings = SettingsManager("automation", defaults=AUTOMATION_DEFAULTS)
        self._meshtastic_host = meshtastic_host

        self._running = False
        self._stop_event = threading.Event()
        self._threads: Dict[str, threading.Thread] = {}

        # Rate limiting
        self._last_request_time: float = 0
        self._rate_lock = threading.Lock()

        # Ping history (last N results per node)
        self._ping_history: Dict[str, List[PingResult]] = {}
        self._ping_lock = threading.Lock()

        # Traceroute history
        self._traceroute_history: Dict[str, List[TracerouteResult]] = {}
        self._traceroute_lock = threading.Lock()

        # Welcome tracking (nodes we've already greeted)
        self._welcomed_nodes: Set[str] = set()
        self._welcome_lock = threading.Lock()

        # Statistics
        self._stats = {
            "pings_sent": 0,
            "pings_success": 0,
            "pings_failed": 0,
            "traceroutes_sent": 0,
            "traceroutes_success": 0,
            "traceroutes_failed": 0,
            "welcomes_sent": 0,
            "last_ping_cycle": None,
            "last_traceroute_cycle": None,
            "last_welcome_check": None,
        }
        self._stats_lock = threading.Lock()

    def start(self) -> bool:
        """Start all enabled automation tasks."""
        if self._running:
            logger.warning("AutomationEngine already running")
            return True

        self._running = True
        self._stop_event.clear()

        config = self._settings.all()

        if config.get("auto_ping", {}).get("enabled", False):
            self._start_thread("ping", self._ping_loop)

        if config.get("auto_traceroute", {}).get("enabled", False):
            self._start_thread("traceroute", self._traceroute_loop)

        if config.get("auto_welcome", {}).get("enabled", False):
            self._start_thread("welcome", self._welcome_loop)

        if not self._threads:
            logger.info("AutomationEngine: no tasks enabled")
            self._running = False
            return False

        logger.info(
            f"AutomationEngine started with tasks: "
            f"{', '.join(self._threads.keys())}"
        )
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop all automation tasks."""
        if not self._running:
            return

        logger.info("Stopping AutomationEngine...")
        self._running = False
        self._stop_event.set()

        for name, thread in self._threads.items():
            if thread.is_alive():
                thread.join(timeout=timeout)
                if thread.is_alive():
                    logger.warning(f"Automation thread '{name}' did not stop in time")

        self._threads.clear()
        logger.info("AutomationEngine stopped")

    def is_alive(self) -> bool:
        """Check if any automation threads are running."""
        return self._running and any(t.is_alive() for t in self._threads.values())

    def get_status(self) -> dict:
        """Get current automation status."""
        config = self._settings.all()
        with self._stats_lock:
            stats = dict(self._stats)

        return {
            "running": self._running,
            "active_threads": [
                name for name, t in self._threads.items() if t.is_alive()
            ],
            "config": config,
            "stats": stats,
        }

    def get_settings(self) -> SettingsManager:
        """Get the settings manager for external configuration."""
        return self._settings

    def get_ping_history(self, node_id: Optional[str] = None) -> dict:
        """Get ping history, optionally for a specific node."""
        with self._ping_lock:
            if node_id:
                return {node_id: list(self._ping_history.get(node_id, []))}
            return {k: list(v) for k, v in self._ping_history.items()}

    def get_traceroute_history(self, node_id: Optional[str] = None) -> dict:
        """Get traceroute history, optionally for a specific node."""
        with self._traceroute_lock:
            if node_id:
                return {node_id: list(self._traceroute_history.get(node_id, []))}
            return {k: list(v) for k, v in self._traceroute_history.items()}

    # --- Internal helpers ---

    def _start_thread(self, name: str, target) -> None:
        """Start a named daemon thread."""
        thread = threading.Thread(
            target=target,
            daemon=True,
            name=f"Automation-{name}",
        )
        thread.start()
        self._threads[name] = thread

    def _can_send_request(self) -> bool:
        """Rate-limit outbound requests."""
        with self._rate_lock:
            now = time.monotonic()
            if now - self._last_request_time < MIN_REQUEST_INTERVAL_SECONDS:
                return False
            self._last_request_time = now
            return True

    # --- Ping loop ---

    def _ping_loop(self) -> None:
        """Periodically ping configured target nodes."""
        config = self._settings.get("auto_ping", {})
        interval = config.get("interval_minutes", 15) * 60
        targets = config.get("targets", [])
        timeout = config.get("timeout_seconds", 30)

        logger.info(
            f"Auto-ping started: {len(targets)} targets, "
            f"interval {interval // 60}min"
        )

        while self._running:
            for node_id in targets:
                if not self._running:
                    break
                if not self._can_send_request():
                    # Wait for rate limit to clear
                    if self._stop_event.wait(timeout=MIN_REQUEST_INTERVAL_SECONDS):
                        return
                    continue

                result = self._send_ping(node_id, timeout)
                self._record_ping(result)

            with self._stats_lock:
                self._stats["last_ping_cycle"] = datetime.now().isoformat()

            if self._stop_event.wait(timeout=interval):
                return

    def _send_ping(self, node_id: str, timeout: int = 30) -> PingResult:
        """Send a ping to a node via meshtastic CLI."""
        start = time.monotonic()
        try:
            result = subprocess.run(
                [
                    "meshtastic",
                    "--host", self._meshtastic_host,
                    "--sendping",
                    "--dest", node_id,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            success = result.returncode == 0

            with self._stats_lock:
                self._stats["pings_sent"] += 1
                if success:
                    self._stats["pings_success"] += 1
                else:
                    self._stats["pings_failed"] += 1

            return PingResult(
                node_id=node_id,
                timestamp=datetime.now(),
                success=success,
                latency_ms=elapsed_ms if success else None,
                error=result.stderr.strip() if not success else None,
            )
        except subprocess.TimeoutExpired:
            with self._stats_lock:
                self._stats["pings_sent"] += 1
                self._stats["pings_failed"] += 1
            return PingResult(
                node_id=node_id,
                timestamp=datetime.now(),
                success=False,
                error=f"Timeout after {timeout}s",
            )
        except Exception as e:
            with self._stats_lock:
                self._stats["pings_sent"] += 1
                self._stats["pings_failed"] += 1
            return PingResult(
                node_id=node_id,
                timestamp=datetime.now(),
                success=False,
                error=str(e),
            )

    def _record_ping(self, result: PingResult) -> None:
        """Record a ping result, keeping last 100 per node."""
        with self._ping_lock:
            if result.node_id not in self._ping_history:
                self._ping_history[result.node_id] = []
            history = self._ping_history[result.node_id]
            history.append(result)
            if len(history) > 100:
                self._ping_history[result.node_id] = history[-100:]

        status = "OK" if result.success else f"FAIL: {result.error}"
        latency = f" ({result.latency_ms:.0f}ms)" if result.latency_ms else ""
        logger.debug(f"Auto-ping {result.node_id}: {status}{latency}")

    # --- Traceroute loop ---

    def _traceroute_loop(self) -> None:
        """Periodically trace routes to configured target nodes."""
        config = self._settings.get("auto_traceroute", {})
        interval = config.get("interval_minutes", 60) * 60
        targets = config.get("targets", [])
        timeout = config.get("timeout_seconds", 60)

        logger.info(
            f"Auto-traceroute started: {len(targets)} targets, "
            f"interval {interval // 60}min"
        )

        while self._running:
            for node_id in targets:
                if not self._running:
                    break
                if not self._can_send_request():
                    if self._stop_event.wait(timeout=MIN_REQUEST_INTERVAL_SECONDS):
                        return
                    continue

                result = self._send_traceroute(node_id, timeout)
                self._record_traceroute(result)

            with self._stats_lock:
                self._stats["last_traceroute_cycle"] = datetime.now().isoformat()

            if self._stop_event.wait(timeout=interval):
                return

    def _send_traceroute(self, node_id: str, timeout: int = 60) -> TracerouteResult:
        """Send a traceroute to a node via meshtastic CLI."""
        try:
            result = subprocess.run(
                [
                    "meshtastic",
                    "--host", self._meshtastic_host,
                    "--traceroute", node_id,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            success = result.returncode == 0
            output = result.stdout.strip()

            # Count hops from output (lines with node IDs)
            hops = 0
            if success and output:
                for line in output.split("\n"):
                    if "!" in line or "node" in line.lower():
                        hops += 1

            with self._stats_lock:
                self._stats["traceroutes_sent"] += 1
                if success:
                    self._stats["traceroutes_success"] += 1
                else:
                    self._stats["traceroutes_failed"] += 1

            return TracerouteResult(
                node_id=node_id,
                timestamp=datetime.now(),
                success=success,
                hops=hops,
                output=output,
                error=result.stderr.strip() if not success else None,
            )
        except subprocess.TimeoutExpired:
            with self._stats_lock:
                self._stats["traceroutes_sent"] += 1
                self._stats["traceroutes_failed"] += 1
            return TracerouteResult(
                node_id=node_id,
                timestamp=datetime.now(),
                success=False,
                error=f"Timeout after {timeout}s",
            )
        except Exception as e:
            with self._stats_lock:
                self._stats["traceroutes_sent"] += 1
                self._stats["traceroutes_failed"] += 1
            return TracerouteResult(
                node_id=node_id,
                timestamp=datetime.now(),
                success=False,
                error=str(e),
            )

    def _record_traceroute(self, result: TracerouteResult) -> None:
        """Record a traceroute result, keeping last 50 per node."""
        with self._traceroute_lock:
            if result.node_id not in self._traceroute_history:
                self._traceroute_history[result.node_id] = []
            history = self._traceroute_history[result.node_id]
            history.append(result)
            if len(history) > 50:
                self._traceroute_history[result.node_id] = history[-50:]

        status = f"{result.hops} hops" if result.success else f"FAIL: {result.error}"
        logger.debug(f"Auto-traceroute {result.node_id}: {status}")

    # --- Welcome loop ---

    def _welcome_loop(self) -> None:
        """Monitor for new nodes and send welcome messages."""
        config = self._settings.get("auto_welcome", {})
        message = config.get("message", "Welcome to the mesh!")
        cooldown_hours = config.get("cooldown_hours", 24)
        check_interval = 60  # Check every minute for new nodes

        logger.info(
            f"Auto-welcome started: cooldown {cooldown_hours}h, "
            f"check interval {check_interval}s"
        )

        while self._running:
            if _HAS_NODE_TRACKER and _get_node_tracker:
                try:
                    tracker = _get_node_tracker()
                    if tracker:
                        self._check_new_nodes(tracker, message)
                except Exception as e:
                    logger.debug(f"Auto-welcome check failed: {e}")

            with self._stats_lock:
                self._stats["last_welcome_check"] = datetime.now().isoformat()

            if self._stop_event.wait(timeout=check_interval):
                return

    def _check_new_nodes(self, tracker, message: str) -> None:
        """Check for new nodes and send welcome messages."""
        try:
            nodes = tracker.get_all_nodes() if hasattr(tracker, 'get_all_nodes') else {}
        except Exception:
            return

        for node_id in nodes:
            if not self._running:
                break

            with self._welcome_lock:
                if node_id in self._welcomed_nodes:
                    continue
                self._welcomed_nodes.add(node_id)

            if not self._can_send_request():
                if self._stop_event.wait(timeout=MIN_REQUEST_INTERVAL_SECONDS):
                    return
                continue

            self._send_welcome(node_id, message)

    def _send_welcome(self, node_id: str, message: str) -> None:
        """Send a welcome message to a new node."""
        try:
            result = subprocess.run(
                [
                    "meshtastic",
                    "--host", self._meshtastic_host,
                    "--sendtext", message,
                    "--dest", node_id,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                with self._stats_lock:
                    self._stats["welcomes_sent"] += 1
                logger.info(f"Welcomed new node {node_id}")
            else:
                logger.warning(
                    f"Failed to welcome {node_id}: {result.stderr.strip()}"
                )
        except subprocess.TimeoutExpired:
            logger.warning(f"Welcome to {node_id} timed out")
        except Exception as e:
            logger.warning(f"Welcome to {node_id} failed: {e}")


# Module-level singleton
_engine: Optional[AutomationEngine] = None
_engine_lock = threading.Lock()


def get_automation_engine() -> AutomationEngine:
    """Get or create the global AutomationEngine instance."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = AutomationEngine()
    return _engine
