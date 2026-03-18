"""Security diagnostic rules for MeshForge Diagnostic Engine."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.diagnostic_engine import DiagnosticEngine

from utils.diagnostic_engine import (
    Category,
    DiagnosticRule,
)


def load_security_rules(engine: "DiagnosticEngine") -> None:
    """Load security diagnostic rules."""

    engine.add_rule(DiagnosticRule(
        name="unauthorized_access_attempt",
        pattern=r"(?i)(unauthorized|auth|login).*(attempt|failed|denied|invalid|rejected)",
        category=Category.SECURITY,
        cause_template="Unauthorized access attempt detected",
        suggestions=[
            "Review access logs for source of attempts",
            "Verify credentials are correct",
            "Check for brute-force patterns",
            "Consider enabling fail2ban if repeated",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="rogue_node_detected",
        pattern=r"(?i)(rogue|unknown|unexpected|foreign).*(node|device|station).*detect",
        category=Category.SECURITY,
        cause_template="Unknown node detected on mesh network — may be unauthorized",
        suggestions=[
            "Verify node ID against known device inventory",
            "Check if new node was authorized by network admin",
            "Enable encryption on all channels",
            "Monitor node for suspicious activity patterns",
        ],
        confidence_base=0.7,
    ))

    engine.add_rule(DiagnosticRule(
        name="key_rotation_needed",
        pattern=r"(?i)(key|certificate|credential).*(expir|rotat|old|stale|renew)",
        category=Category.SECURITY,
        cause_template="Encryption keys or certificates need rotation",
        suggestions=[
            "Rotate channel encryption keys on all nodes",
            "Update TLS certificates before expiry",
            "Regenerate RNS identity if compromised",
            "Document new key distribution to all operators",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="insecure_channel_detected",
        pattern=r"(?i)(channel|link).*(unencrypted|insecure|plaintext|no.*encrypt)",
        category=Category.SECURITY,
        cause_template="Communication channel is not encrypted — messages visible to anyone",
        suggestions=[
            "Enable encryption: meshtastic --ch-set psk random",
            "Use AES256 for sensitive communications",
            "Share keys only through secure channels",
            "Default PSK 'AQ==' is publicly known — always change it",
        ],
        confidence_base=0.9,
    ))
