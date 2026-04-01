"""
Demo Mode Manager — Simulated mesh traffic for hardware-free testing.

Uses meshing_around's MockMeshtasticAPI when available, otherwise provides
a minimal built-in simulation. All events are translated to MeshAnchor's
event_bus types (MessageEvent, NodeEvent, AlertEvent).

Degrades gracefully if meshing_around is not installed.
"""

import logging
import random
import sys
import threading
import time
from datetime import datetime
from typing import Dict, Optional

from utils.event_bus import (
    emit_alert,
    emit_message,
    emit_node_update,
)
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Add meshing_around to path if available
_MA_PATH = "/opt/meshing_around_meshanchor"
if _MA_PATH not in sys.path:
    sys.path.insert(0, _MA_PATH)

# Try importing MockMeshtasticAPI
_MockAPI, _Config, _HAS_MOCK = safe_import(
    'meshing_around_clients.core.meshtastic_api', 'MockMeshtasticAPI', 'Config'
)

# Fallback demo data for when meshing_around isn't available
_DEMO_NODES = [
    ("!abc12345", "BaseStation", "HQ Base Station"),
    ("!def67890", "Mobile1", "Field Unit Alpha"),
    ("!fed98765", "Relay", "Mountain Repeater"),
    ("!123abcde", "Solar1", "Solar Powered Node"),
    ("!456f0e1a", "Router", "Community Router"),
]

_DEMO_MESSAGES = [
    "Anyone copy?",
    "Signal check - how's my SNR?",
    "Heading to the trailhead, back in 2h",
    "Weather looks clear from up here",
    "Battery swap complete, back online",
    "Repeater seems solid today",
    "New firmware is working great",
    "Copy that, loud and clear",
    "Roger, standing by",
    "Testing range from the ridge",
    "Good morning mesh!",
]


class DemoModeManager:
    """Manages simulated mesh traffic for demonstration and testing."""

    def __init__(self):
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._mock_api = None
        self._stats = {"node_count": 0, "message_count": 0, "alert_count": 0}
        self._stats_lock = threading.Lock()

    def is_running(self) -> bool:
        return self._running

    def get_stats(self) -> Dict:
        with self._stats_lock:
            return dict(self._stats)

    def start(self) -> bool:
        """Start demo mode. Returns True on success."""
        if self._running:
            return True

        self._stop_event.clear()
        self._stats = {"node_count": 0, "message_count": 0, "alert_count": 0}

        if _HAS_MOCK and _MockAPI and _Config:
            return self._start_with_mock_api()
        return self._start_builtin()

    def _start_with_mock_api(self) -> bool:
        """Start using meshing_around's MockMeshtasticAPI."""
        try:
            # Create config programmatically (avoid Path.home() in Config())
            config = _Config.__new__(_Config)
            config.interface_type = "mock"
            config.host = "localhost"
            config.port = 4403
            config.node_name = "MeshAnchor Demo"

            self._mock_api = _MockAPI(config)

            # Register callbacks that translate to MeshAnchor event_bus
            self._mock_api.register_callback("on_message", self._on_ma_message)
            self._mock_api.register_callback("on_node_update", self._on_ma_node_update)
            self._mock_api.register_callback("on_alert", self._on_ma_alert)
            self._mock_api.register_callback("on_telemetry", self._on_ma_telemetry)

            self._mock_api.connect()
            self._running = True

            with self._stats_lock:
                self._stats["node_count"] = len(
                    getattr(self._mock_api, 'network', None)
                    and self._mock_api.network.nodes or {}
                )

            logger.info("Demo mode started (meshing_around MockAPI)")
            return True

        except Exception as e:
            logger.warning("MockAPI start failed, falling back to builtin: %s", e)
            self._mock_api = None
            return self._start_builtin()

    def _start_builtin(self) -> bool:
        """Start with built-in minimal simulation."""
        self._running = True
        self._thread = threading.Thread(
            target=self._builtin_traffic_loop,
            daemon=True,
            name="demo-traffic",
        )
        self._thread.start()

        # Emit initial nodes
        for node_id, short, long_name in _DEMO_NODES:
            emit_node_update(
                event_type="discovered",
                node_id=node_id,
                node_name=long_name,
            )
        with self._stats_lock:
            self._stats["node_count"] = len(_DEMO_NODES)

        logger.info("Demo mode started (built-in simulation)")
        return True

    def _builtin_traffic_loop(self) -> None:
        """Background loop generating simulated traffic."""
        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=random.uniform(5.0, 15.0)):
                break

            node_id, short, long_name = random.choice(_DEMO_NODES)
            text = random.choice(_DEMO_MESSAGES)

            emit_message(
                direction="rx",
                content=text,
                node_id=node_id,
                node_name=long_name,
                channel=0,
                network="meshtastic",
            )

            with self._stats_lock:
                self._stats["message_count"] += 1

            # Occasional battery alert
            if random.random() < 0.05:
                battery = random.randint(5, 18)
                emit_alert(
                    alert_type="battery",
                    title=f"Low battery: {long_name}",
                    message=f"{long_name} at {battery}%",
                    severity=2,
                    source_node=node_id,
                    metadata={"battery_level": battery},
                )
                with self._stats_lock:
                    self._stats["alert_count"] += 1

    # ── meshing_around callback translators ─────────────────────

    def _on_ma_message(self, *args) -> None:
        """Translate meshing_around message to MeshAnchor event_bus."""
        if not args:
            return
        msg = args[0] if len(args) == 1 else args
        text = getattr(msg, 'text', str(msg))
        sender = getattr(msg, 'sender_name', '') or getattr(msg, 'sender_id', '')
        node_id = getattr(msg, 'sender_id', '')

        emit_message(
            direction="rx",
            content=text,
            node_id=node_id,
            node_name=sender,
            network="meshtastic",
        )
        with self._stats_lock:
            self._stats["message_count"] += 1

    def _on_ma_node_update(self, *args) -> None:
        """Translate meshing_around node update to MeshAnchor event_bus."""
        if not args:
            return
        node_id = args[0] if isinstance(args[0], str) else getattr(args[0], 'node_id', '')
        is_new = args[1] if len(args) > 1 else False

        emit_node_update(
            event_type="discovered" if is_new else "updated",
            node_id=node_id,
        )
        if is_new:
            with self._stats_lock:
                self._stats["node_count"] += 1

    def _on_ma_alert(self, *args) -> None:
        """Translate meshing_around alert to MeshAnchor event_bus."""
        if not args:
            return
        alert = args[0]
        emit_alert(
            alert_type=getattr(alert, 'alert_type', 'custom'),
            title=getattr(alert, 'title', 'Demo Alert'),
            message=getattr(alert, 'message', ''),
            severity=getattr(alert, 'severity', 2),
            source_node=getattr(alert, 'source_node', ''),
        )
        with self._stats_lock:
            self._stats["alert_count"] += 1

    def _on_ma_telemetry(self, *args) -> None:
        """Translate meshing_around telemetry to MeshAnchor node update."""
        if not args:
            return
        node_id = args[0] if isinstance(args[0], str) else getattr(args[0], 'node_id', '')
        emit_node_update(
            event_type="updated",
            node_id=node_id,
        )

    def stop(self) -> None:
        """Stop demo mode."""
        if not self._running:
            return

        self._stop_event.set()

        if self._mock_api:
            try:
                self._mock_api.disconnect()
            except Exception as e:
                logger.debug("MockAPI disconnect error: %s", e)
            self._mock_api = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

        self._running = False
        logger.info("Demo mode stopped")


# ── Module-level singleton ──────────────────────────────────────

_manager: Optional[DemoModeManager] = None
_manager_lock = threading.Lock()


def get_demo_manager() -> DemoModeManager:
    """Get or create the singleton DemoModeManager."""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = DemoModeManager()
    return _manager
