"""Protocol diagnostic rules for MeshAnchor Diagnostic Engine."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.diagnostic_engine import DiagnosticEngine

from utils.diagnostic_engine import (
    Category,
    DiagnosticRule,
)


def load_protocol_rules(engine: "DiagnosticEngine") -> None:
    """Load protocol diagnostic rules."""

    engine.add_rule(DiagnosticRule(
        name="encryption_mismatch",
        pattern=r"(?i)(encryption|decrypt|key).*(mismatch|failed|invalid|error)",
        category=Category.PROTOCOL,
        cause_template="Encryption key mismatch between nodes",
        suggestions=[
            "Verify channel encryption key matches on all nodes",
            "Check channel settings: meshtastic --ch-index 0 --info",
            "Reset to default key if needed: meshtastic --ch-set psk default",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="protocol_version_mismatch",
        pattern=r"(?i)(protocol|version|firmware).*(mismatch|incompatible|unsupported)",
        category=Category.PROTOCOL,
        cause_template="Protocol or firmware version incompatibility",
        suggestions=[
            "Update firmware on all nodes to same version",
            "Check firmware: meshtastic --info | grep firmware",
            "See https://meshtastic.org/docs/getting-started/flashing-firmware",
        ],
        confidence_base=0.8,
    ))

    # ── Extended protocol rules ──

    engine.add_rule(DiagnosticRule(
        name="channel_config_mismatch",
        pattern=r"(?i)(channel|freq|frequency).*(mismatch|wrong|different|hop)",
        category=Category.PROTOCOL,
        cause_template="Nodes are on different channels or frequencies",
        suggestions=[
            "Verify all nodes use same channel config",
            "Check channel index: meshtastic --ch-index 0 --info",
            "Share channel via QR code or URL for consistency",
            "Check for accidental frequency offset",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="crc_error_high",
        pattern=r"(?i)(crc|checksum|integrity).*(error|fail|mismatch|corrupt|high rate)",
        category=Category.PROTOCOL,
        cause_template="High CRC error rate indicating RF interference or hardware issue",
        suggestions=[
            "Check for nearby RF interference sources",
            "Verify antenna connection (loose SMA = high CRC)",
            "Try different frequency/channel to avoid interference",
            "Check if nodes are too close (receiver saturation)",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="mesh_routing_loop",
        pattern=r"(?i)(routing|route|mesh).*(loop|circular|infinite|ttl.*expir)",
        category=Category.PROTOCOL,
        cause_template="Message routing loop detected — packets circling without delivery",
        suggestions=[
            "Check hop limit settings (default 3, max 7)",
            "Verify no duplicate node IDs in mesh",
            "Reboot nodes to clear stale routing tables",
            "Check for misconfigured relay/router roles",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="beacon_timeout",
        pattern=r"(?i)(beacon|heartbeat|keepalive|announce).*(timeout|missed|expired|lost)",
        category=Category.PROTOCOL,
        cause_template="Node beacon/heartbeat not received — node may be offline or out of range",
        suggestions=[
            "Check if node is powered on and operational",
            "Verify RF path between nodes",
            "Node may have moved out of range",
            "Check node's configured beacon interval",
        ],
        confidence_base=0.7,
    ))

    engine.add_rule(DiagnosticRule(
        name="identity_collision",
        pattern=r"(?i)(identity|hash|address).*(collision|duplicate|conflict)",
        category=Category.PROTOCOL,
        cause_template="Identity hash collision — two nodes resolving to same address",
        suggestions=[
            "This is extremely rare — verify it's a real collision",
            "One node should regenerate identity",
            "For RNS: delete identity file and restart",
            "Check for cloned devices (same keys on multiple nodes)",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="lxmf_delivery_failed",
        pattern=r"(?i)(lxmf|message|delivery).*(failed|timeout|undelivered|expired)",
        category=Category.PROTOCOL,
        cause_template="LXMF message delivery failed — destination unreachable or not accepting",
        suggestions=[
            "Check if destination node is online and reachable",
            "Verify LXMF propagation node is available",
            "Message may be queued for later delivery",
            "Check message size (large messages need more airtime)",
        ],
        auto_recoverable=True,
        recovery_action="Message queued for retry on next contact",
        confidence_base=0.75,
    ))

    engine.add_rule(DiagnosticRule(
        name="packet_decode_error",
        pattern=r"(?i)(packet|frame|message).*(decode|deseriali|unmarshal|parse).*(error|fail)",
        category=Category.PROTOCOL,
        cause_template="Received packet could not be decoded — possible version mismatch or corruption",
        suggestions=[
            "Check firmware versions match across nodes",
            "Verify encryption keys are synchronized",
            "May be interference corrupting packets",
            "Check for mixed Meshtastic protocol versions",
        ],
        confidence_base=0.75,
    ))
