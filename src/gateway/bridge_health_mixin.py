"""Subsystem-state / circuit-breaker / status surface for RNSMeshtasticBridge.

Part of the 2026-06-09 rns_bridge.py split (MeshForge parity — the same
mixin seams land in the sister repo). RNSMeshtasticBridge is the only
consumer; methods keep their pre-extraction signatures so attribute
access via the bridge instance/class is unchanged.

Host class must provide:
- self.health (BridgeHealthMonitor)
- self._circuit_breaker (CircuitBreakerRegistry or None)
- self._mesh_handler / self._meshcore_handler (or None)
- self._connected_rns, self._rns_via_rnsd, self._rns_init_failed_permanently
- self._running, self.config, self.stats, self.node_tracker
- self._test_rns()
"""

import logging
from datetime import datetime
from typing import Any, Dict

from .bridge_health import BridgeStatus, SubsystemState

logger = logging.getLogger(__name__)


class BridgeHealthMixin:
    """Mixin: subsystem state management + circuit breakers (Phase 2)."""

    @property
    def bridge_status(self) -> BridgeStatus:
        """Get current bridge operational status."""
        return self.health.get_bridge_status()

    def _update_subsystem_state(self, subsystem: str, state: SubsystemState) -> None:
        """Update a subsystem's state and emit an event if it changed.

        Args:
            subsystem: "meshtastic" or "rns"
            state: New SubsystemState value
        """
        old_state = self.health.set_subsystem_state(subsystem, state)
        if old_state != state:
            # Emit event for StatusBar and other listeners.
            # Lazy read back through rns_bridge so tests patching
            # gateway.rns_bridge.HAS_EVENT_BUS keep working after the
            # split (same idiom as the MeshForge _rns_bridge_xform
            # HAS_PERSISTENT_QUEUE re-read).
            from . import rns_bridge as _rns_bridge_module
            if _rns_bridge_module.HAS_EVENT_BUS:
                try:
                    from utils.event_bus import emit_service_status
                    emit_service_status(
                        f"bridge_{subsystem}",
                        available=(state == SubsystemState.HEALTHY),
                        message=f"{subsystem}: {state.value}",
                    )
                except Exception as e:
                    logger.debug(f"Failed to emit subsystem state event: {e}")

    def get_subsystem_state(self, subsystem: str) -> SubsystemState:
        """Get the current state of a bridge subsystem.

        Args:
            subsystem: "meshtastic", "rns", or "meshcore"

        Returns:
            Current SubsystemState.
        """
        return self.health.get_subsystem_state(subsystem)

    @property
    def is_fully_healthy(self) -> bool:
        """Check if bridge is fully operational (both networks up)."""
        return self.health.is_bridge_fully_healthy()

    def can_send_to(self, destination: str) -> bool:
        """
        Check if we can send to a destination (circuit breaker check).

        Args:
            destination: Target node/identity ID

        Returns:
            True if sending is allowed, False if circuit is open
        """
        if self._circuit_breaker is None:
            return True
        return self._circuit_breaker.can_send(destination)

    def record_send_success(self, destination: str) -> None:
        """Record successful send to destination (for circuit breaker)."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_success(destination)

    def record_send_failure(self, destination: str, error: str = "") -> None:
        """Record failed send to destination (for circuit breaker)."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_failure(destination, error)

    def get_open_circuits(self) -> Dict[str, Any]:
        """Get destinations with open circuits (currently blocked)."""
        if self._circuit_breaker is None:
            return {}
        return self._circuit_breaker.get_open_circuits()

    def get_status(self) -> dict:
        """Get current bridge status including subsystem states."""
        uptime = None
        if self.stats['start_time']:
            uptime = (datetime.now() - self.stats['start_time']).total_seconds()

        mesh_connected = self._mesh_handler.is_connected if self._mesh_handler else False
        meshcore_connected = (
            self._meshcore_handler.is_connected if self._meshcore_handler else False
        )
        meshcore_config = getattr(self.config, 'meshcore', None)
        return {
            'running': self._running,
            'enabled': self.config.enabled,
            'meshtastic_connected': mesh_connected,
            'rns_connected': self._connected_rns,
            'rns_via_rnsd': self._rns_via_rnsd,
            'meshcore_connected': meshcore_connected,
            'meshcore_enabled': bool(meshcore_config and meshcore_config.enabled),
            'uptime_seconds': uptime,
            'statistics': self.stats.copy(),
            'node_stats': self.node_tracker.get_stats(),
            'subsystems': self.health.get_subsystem_states(),
            'bridge_status': self.bridge_status.value,
        }

    def test_connection(self) -> dict:
        """Test connectivity to all configured networks"""
        results = {
            'meshtastic': {'connected': False, 'error': None},
            'rns': {'connected': False, 'error': None},
        }

        # Test Meshtastic
        try:
            if self._mesh_handler and self._mesh_handler.test_connection():
                results['meshtastic']['connected'] = True
        except Exception as e:
            results['meshtastic']['error'] = str(e)

        # Test RNS
        try:
            if self._test_rns():
                results['rns']['connected'] = True
        except Exception as e:
            results['rns']['error'] = str(e)

        # Test MeshCore (if enabled)
        if self._meshcore_handler:
            results['meshcore'] = {'connected': False, 'error': None}
            try:
                if self._meshcore_handler.is_connected:
                    results['meshcore']['connected'] = True
            except Exception as e:
                results['meshcore']['error'] = str(e)

        return results

    def _sync_subsystem_states(self) -> None:
        """Synchronize subsystem states from connection status.

        Called each bridge loop iteration. Both handlers manage their own
        reconnection, so we observe connection states and update accordingly.
        The RNS subsystem state is also updated in _rns_loop, but we sync
        here too so the bridge loop has accurate state even when _rns_loop
        is not running (e.g., in tests).
        """
        # Meshtastic
        if not self._mesh_handler:
            self._update_subsystem_state("meshtastic", SubsystemState.DISABLED)
        elif self._mesh_handler.is_connected:
            self._update_subsystem_state("meshtastic", SubsystemState.HEALTHY)
        else:
            self._update_subsystem_state("meshtastic", SubsystemState.DISCONNECTED)

        # RNS (also managed by _rns_loop, but kept in sync here)
        if self._rns_init_failed_permanently:
            self._update_subsystem_state("rns", SubsystemState.DISABLED)
        elif self._connected_rns:
            self._update_subsystem_state("rns", SubsystemState.HEALTHY)
        # Note: don't overwrite DISCONNECTED here — _rns_loop handles transitions

        # MeshCore
        if not self._meshcore_handler:
            self._update_subsystem_state("meshcore", SubsystemState.DISABLED)
        elif self._meshcore_handler.is_connected:
            self._update_subsystem_state("meshcore", SubsystemState.HEALTHY)
        else:
            self._update_subsystem_state("meshcore", SubsystemState.DISCONNECTED)
