"""Connectivity diagnostic rules for MeshForge Diagnostic Engine."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.diagnostic_engine import DiagnosticEngine

from utils.diagnostic_engine import (
    Category,
    DiagnosticRule,
    check_meshtasticd_clients,
    make_port_check,
    make_process_check,
    make_service_active_check,
)


def load_connectivity_rules(engine: "DiagnosticEngine") -> None:
    """Load connectivity diagnostic rules."""

    engine.add_rule(DiagnosticRule(
        name="meshtasticd_connection_refused",
        pattern=r"(?i)connection\s+(refused|rejected).*meshtastic",
        category=Category.CONNECTIVITY,
        cause_template="Another client is likely connected to meshtasticd (single-client limitation)",
        evidence_checks=[
            make_port_check("localhost", 4403),  # Verify port is actually open
            lambda ctx: check_meshtasticd_clients(),  # Check for other clients
            make_service_active_check("meshtasticd"),  # Verify service running
        ],
        suggestions=[
            "Check for other Meshtastic clients: ps aux | grep -E 'nomadnet|meshing|meshtastic'",
            "Restart meshtasticd: sudo systemctl restart meshtasticd",
            "Use --host to connect to a different instance",
        ],
        auto_recoverable=True,
        recovery_action="Will attempt reconnection with backoff",
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="meshtasticd_not_running",
        pattern=r"(?i)meshtastic.*(not running|stopped|inactive|dead|failed to start)",
        category=Category.CONNECTIVITY,
        cause_template="meshtasticd service is not running",
        evidence_checks=[
            make_service_active_check("meshtasticd"),
        ],
        suggestions=[
            "Start meshtasticd: sudo systemctl start meshtasticd",
            "Check logs: sudo journalctl -u meshtasticd -n 30",
            "Verify config: cat /etc/meshtasticd/config.yaml",
        ],
        auto_recoverable=True,
        recovery_action="sudo systemctl restart meshtasticd",
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="rns_transport_unavailable",
        pattern=r"(?i)(rns|reticulum).*(transport|interface).*(unavailable|failed|error|down)",
        category=Category.CONNECTIVITY,
        cause_template="RNS transport interface failed to initialize",
        evidence_checks=[
            make_service_active_check("rnsd"),
        ],
        suggestions=[
            "Check rnsd status: sudo systemctl status rnsd",
            "Check RNS interfaces: rnstatus",
            "Verify Reticulum config: cat ~/.reticulum/config",
            "Restart rnsd: sudo systemctl restart rnsd",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="mqtt_connection_failed",
        pattern=r"(?i)mqtt.*(connection|connect).*(failed|refused|error|timeout|lost)",
        category=Category.CONNECTIVITY,
        cause_template="MQTT broker connection failed",
        suggestions=[
            "Check broker is running: systemctl status mosquitto",
            "Test connectivity: mosquitto_sub -t 'test' -C 1 -W 3",
            "Verify credentials in config",
            "Check firewall: sudo ufw status",
        ],
        auto_recoverable=True,
        recovery_action="Will retry MQTT connection with exponential backoff",
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="node_timeout",
        pattern=r"(?i)(node|device|peer).*(timeout|not responding|unreachable|timed out)",
        category=Category.CONNECTIVITY,
        cause_template="Remote node is not responding",
        suggestions=[
            "Verify node is powered and in range",
            "Check mesh route to node",
            "Node may be in sleep mode",
            "Try direct message to trigger response",
        ],
        confidence_base=0.7,
    ))

    # ── Extended connectivity rules ──

    engine.add_rule(DiagnosticRule(
        name="dns_resolution_failed",
        pattern=r"(?i)(dns|resolve|lookup|hostname).*(failed|error|timeout|not found)",
        category=Category.CONNECTIVITY,
        cause_template="DNS resolution failed — cannot resolve hostname",
        suggestions=[
            "Check internet connectivity: ping 8.8.8.8",
            "Verify DNS settings: cat /etc/resolv.conf",
            "Try alternative DNS: echo 'nameserver 8.8.8.8' | sudo tee /etc/resolv.conf",
            "Check if running in offline/air-gapped mode",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="network_interface_down",
        pattern=r"(?i)(interface|eth|wlan|wifi|network).*(down|no carrier|link.*lost|disconnected)",
        category=Category.CONNECTIVITY,
        cause_template="Network interface is down or has no link",
        suggestions=[
            "Check interface status: ip link show",
            "Bring interface up: sudo ip link set <iface> up",
            "Check physical cable connection",
            "For WiFi: nmcli device wifi connect <SSID>",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="mqtt_subscription_lost",
        pattern=r"(?i)mqtt.*(subscription|subscribe).*(lost|failed|disconnect|error)",
        category=Category.CONNECTIVITY,
        cause_template="MQTT topic subscription was lost, no longer receiving messages",
        suggestions=[
            "Reconnect to broker and resubscribe",
            "Check broker logs for disconnect reason",
            "Verify topic permissions on broker",
            "Check for client ID conflicts (two clients with same ID)",
        ],
        auto_recoverable=True,
        recovery_action="Will resubscribe to topics on reconnect",
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="rns_path_not_found",
        pattern=r"(?i)(rns|reticulum).*(path|route|destination).*(not found|unknown|no path)",
        category=Category.CONNECTIVITY,
        cause_template="No RNS path exists to the destination — node may be unreachable",
        suggestions=[
            "Check if destination is announcing: rnpath <destination_hash>",
            "Verify transport nodes are online",
            "Wait for path discovery (can take minutes on mesh)",
            "Check if destination has changed identity",
        ],
        confidence_base=0.75,
    ))

    engine.add_rule(DiagnosticRule(
        name="tcp_handshake_timeout",
        pattern=r"(?i)(tcp|connection).*(handshake|establish).*(timeout|timed out|slow)",
        category=Category.CONNECTIVITY,
        cause_template="TCP connection handshake is timing out — remote host unreachable or slow",
        suggestions=[
            "Check if remote host is reachable: ping <host>",
            "Verify firewall rules: sudo iptables -L",
            "Check if service is listening on remote: nc -zv <host> <port>",
            "Increase connection timeout if on slow network",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="bridge_reconnecting",
        pattern=r"(?i)(bridge|gateway).*(reconnect|retry|backoff|attempt \d+)",
        category=Category.CONNECTIVITY,
        cause_template="Bridge is repeatedly reconnecting, indicating unstable connection",
        suggestions=[
            "Check underlying transport (serial, TCP, MQTT) stability",
            "Review bridge health metrics for error patterns",
            "Check for resource exhaustion on bridge host",
            "Verify both endpoints are stable and responsive",
        ],
        auto_recoverable=True,
        recovery_action="Exponential backoff reconnection in progress",
        confidence_base=0.7,
    ))

    engine.add_rule(DiagnosticRule(
        name="websocket_disconnected",
        pattern=r"(?i)(websocket|ws).*(disconnect|closed|error|failed)",
        category=Category.CONNECTIVITY,
        cause_template="WebSocket connection was closed unexpectedly",
        suggestions=[
            "Check if server process is still running",
            "Verify network stability between client and server",
            "Check for proxy/firewall WebSocket blocking",
            "Review server logs for disconnect reason",
        ],
        auto_recoverable=True,
        recovery_action="Will attempt WebSocket reconnection",
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="api_endpoint_unreachable",
        pattern=r"(?i)(api|endpoint|http).*(unreachable|503|502|504|unavailable)",
        category=Category.CONNECTIVITY,
        cause_template="API endpoint is unreachable or returning server errors",
        suggestions=[
            "Check if API service is running",
            "Verify network connectivity to API host",
            "Check for rate limiting (429 responses)",
            "Try alternative endpoint or fallback",
        ],
        auto_recoverable=True,
        recovery_action="Will retry with exponential backoff",
        confidence_base=0.8,
    ))
