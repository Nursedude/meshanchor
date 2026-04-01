"""Meshtastic web client diagnostic rules for MeshAnchor Diagnostic Engine."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.diagnostic_engine import DiagnosticEngine

from utils.diagnostic_engine import (
    Category,
    DiagnosticRule,
    make_port_check,
    make_service_active_check,
)


def load_meshtastic_web_rules(engine: "DiagnosticEngine") -> None:
    """Load Meshtastic web client diagnostic rules."""

    engine.add_rule(DiagnosticRule(
        name="mqtt_downlink_queue_flood",
        pattern=r"(?i)(tophone|to.?phone).*(queue|buffer).*(full|overflow|discard)",
        category=Category.PERFORMANCE,
        cause_template=(
            "MQTT downlink is flooding the device radio queue. "
            "When MQTT downlink is enabled, the broker echoes every published "
            "packet back into the device's tophone queue, overwhelming the radio. "
            "This causes the meshtasticd web client to hang and packets to be dropped."
        ),
        evidence_checks=[
            make_service_active_check("meshtasticd"),
        ],
        suggestions=[
            "Disable MQTT downlink on primary channel: "
            "meshtastic --host localhost --ch-index 0 --ch-set downlink_enabled false",
            "Or reduce MaxMessageQueue in /etc/meshtasticd/config.yaml",
            "Downlink is only needed for MQTT→radio injection (remote apps sending to mesh)",
            "For uplink-only monitoring (mesh→MQTT), downlink should be OFF",
        ],
        auto_recoverable=False,
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="web_client_phantom_node_crash",
        pattern=r"(?i)(web.*client|browser).*(crash|error|embarrass|broke|hang)",
        category=Category.CONFIGURATION,
        cause_template=(
            "The meshtasticd web client crashes when clicking on phantom nodes — "
            "nodes heard via MQTT with incomplete data (no user object, missing role). "
            "The React UI accesses undefined properties and triggers an error boundary. "
            "This is an upstream bug (meshtastic/web#862)."
        ),
        evidence_checks=[
            make_port_check("localhost", 9443),
            make_service_active_check("meshtasticd"),
        ],
        suggestions=[
            "Use MeshAnchor Node DB Cleanup: Meshtasticd > Node DB Cleanup > Scan",
            "Reset node database: meshtastic --host localhost --reset-nodedb",
            "Reduce MaxNodes in config.yaml to limit phantom accumulation",
            "Disable MQTT downlink to stop phantom node ingestion",
            "MeshAnchor API proxy sanitizes nodes when web client is routed through it",
        ],
        auto_recoverable=False,
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="web_client_fromradio_contention",
        pattern=r"(?i)(web.*client|browser).*(hang|freeze|stuck|loading|spinning)",
        category=Category.CONNECTIVITY,
        cause_template=(
            "Multiple clients competing for meshtasticd's /api/v1/fromradio endpoint. "
            "The HTTP API is single-consumer — only one client gets each packet. "
            "If another client (gateway bridge, Python SDK) is connected, the web client "
            "gets starved and hangs waiting for packets that never arrive."
        ),
        evidence_checks=[
            make_port_check("localhost", 9443),
            make_port_check("localhost", 4403),
            make_service_active_check("meshtasticd"),
        ],
        suggestions=[
            "Route web client through MeshAnchor API proxy (multiplexes packets to all clients)",
            "Disconnect other meshtasticd clients before using web UI",
            "Stop gateway bridge temporarily: systemctl stop meshanchor-gateway",
            "Check for MQTT downlink flooding: look for 'tophone queue is full' in logs",
        ],
        auto_recoverable=False,
        confidence_base=0.75,
    ))
