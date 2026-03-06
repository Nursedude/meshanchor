"""
Gateway Heartbeat Handler — Cross-gateway failover status and control.

Provides TUI display for the GatewayHeartbeat: local gateway role/state,
peer status (heartbeat, health score, uptime), and failover event history.
"""

import logging

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

# Optional import — heartbeat requires MQTT
try:
    from gateway.gateway_heartbeat import GatewayHeartbeat, HeartbeatConfig
    _HAS_HEARTBEAT = True
except ImportError:
    _HAS_HEARTBEAT = False


class GatewayHeartbeatHandler(BaseHandler):
    """TUI handler for cross-gateway MQTT heartbeat failover."""

    handler_id = "gateway_heartbeat"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("gateway_heartbeat", "Gateway Heartbeat   Cross-gateway failover", None),
        ]

    def execute(self, action):
        if action == "gateway_heartbeat":
            self._heartbeat_menu()

    def _heartbeat_menu(self):
        """Display gateway heartbeat status and peer information."""
        if not _HAS_HEARTBEAT:
            self.show_message(
                "Gateway Heartbeat",
                "Gateway heartbeat module not available.\n\n"
                "Requires: pip install paho-mqtt"
            )
            return

        # Try to get heartbeat instance from bridge context
        hb = self._get_heartbeat()
        if not hb:
            self.show_message(
                "Gateway Heartbeat",
                "Gateway heartbeat is not active.\n\n"
                "Enable in gateway config:\n"
                "  gateway_heartbeat_enabled: true\n"
                "  gateway_heartbeat_broker: <mqtt-broker-ip>\n"
                "  gateway_role: primary|secondary"
            )
            return

        status = hb.get_status()
        lines = []

        # Local gateway info
        lines.append("=== Local Gateway ===")
        lines.append(f"  ID:         {status['gateway_id']}")
        lines.append(f"  Role:       {status['role']}")
        lines.append(f"  State:      {status['state']}")
        lines.append(f"  MQTT:       {'Connected' if status['mqtt_connected'] else 'Disconnected'}")
        lines.append("")

        # Peer gateways
        peers = status.get('peers', {})
        if peers:
            lines.append("=== Peer Gateways ===")
            for peer_id, peer in peers.items():
                alive_str = "ALIVE" if peer['alive'] else "DOWN"
                lines.append(f"  {peer_id}:")
                lines.append(f"    Status:       {alive_str}")
                lines.append(f"    Role:         {peer['role']}")
                lines.append(f"    Health Score:  {peer['health_score']}")
                lines.append(f"    Missed:       {peer['missed_count']} heartbeats")
                if peer['uptime']:
                    uptime_h = peer['uptime'] / 3600
                    lines.append(f"    Uptime:       {uptime_h:.1f}h")
            lines.append("")
        else:
            lines.append("No peer gateways discovered yet.")
            lines.append("")

        # Recent events
        events = status.get('events', [])
        if events:
            lines.append("=== Recent Events ===")
            for evt in reversed(events[-5:]):
                lines.append(f"  [{evt['timestamp'][:19]}] {evt['type']}: {evt['detail']}")

        self.show_message("Gateway Heartbeat Status", "\n".join(lines))

    def _get_heartbeat(self):
        """Get the active GatewayHeartbeat instance, or None."""
        ctx = getattr(self, 'context', None)
        if ctx:
            bridge = getattr(ctx, 'bridge', None)
            if bridge:
                return getattr(bridge, '_heartbeat', None)
        return None
