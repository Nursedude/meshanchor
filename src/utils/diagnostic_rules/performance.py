"""Performance diagnostic rules for MeshForge Diagnostic Engine."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.diagnostic_engine import DiagnosticEngine

from utils.diagnostic_engine import (
    Category,
    DiagnosticRule,
    make_service_active_check,
)


def load_performance_rules(engine: "DiagnosticEngine") -> None:
    """Load performance diagnostic rules."""

    engine.add_rule(DiagnosticRule(
        name="high_channel_utilization",
        pattern=r"(?i)channel\s*utilization.*(high|>50%|warning)",
        category=Category.PERFORMANCE,
        cause_template="Channel utilization is high, may cause message delays or drops",
        suggestions=[
            "Reduce message frequency",
            "Use shorter messages",
            "Consider different channel or modem preset",
            "Spread nodes across multiple channels",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="message_queue_full",
        pattern=r"(?i)(message|packet|tx)\s*queue.*(full|overflow|backlog|backed up)",
        category=Category.PERFORMANCE,
        cause_template="Message queue is full — outgoing messages are being dropped",
        suggestions=[
            "Reduce message send rate",
            "Check for message storms from automated systems",
            "Increase queue size if hardware allows",
            "Check channel utilization — congestion prevents TX",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="low_snr",
        pattern=r"(?i)snr.*(low|weak|poor|<-10|-\d{2})",
        category=Category.PERFORMANCE,
        cause_template="Signal-to-noise ratio is low, indicating weak signal",
        suggestions=[
            "Improve antenna positioning (height, orientation)",
            "Check for obstructions in RF path",
            "Consider higher gain antenna",
            "Reduce distance or add relay node",
        ],
        confidence_base=0.75,
    ))

    # ── Extended performance rules ──

    engine.add_rule(DiagnosticRule(
        name="high_packet_loss",
        pattern=r"(?i)(packet|message).*(loss|drop|lost|missing).*(high|>\s*[2-9]\d%|\d{2,3}\s*%)",
        category=Category.PERFORMANCE,
        cause_template="High packet loss rate indicating poor RF link or congestion",
        suggestions=[
            "Check signal strength (SNR should be > -10 dB for reliable links)",
            "Improve antenna height or orientation",
            "Reduce message rate to decrease channel congestion",
            "Consider switching to a longer-range LoRa preset",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="latency_spike",
        pattern=r"(?i)(latency|delay|response time).*(spike|high|excessive|>\s*\d+\s*s)",
        category=Category.PERFORMANCE,
        cause_template="Network latency has spiked — congestion or multi-hop delay",
        suggestions=[
            "Check channel utilization (congestion causes queueing delay)",
            "Reduce hop count if possible (each hop adds ~50-200ms)",
            "Check if relay nodes are overloaded",
            "Consider faster modem preset for lower latency",
        ],
        confidence_base=0.75,
    ))

    engine.add_rule(DiagnosticRule(
        name="tx_duty_cycle_exceeded",
        pattern=r"(?i)(duty cycle|tx time|transmit).*(exceed|limit|restrict|legal|regulat)",
        category=Category.PERFORMANCE,
        cause_template="Transmit duty cycle limit exceeded — legally required quiet period",
        suggestions=[
            "Reduce message frequency",
            "Use shorter messages to reduce airtime",
            "Switch to faster modem preset (less airtime per message)",
            "Duty cycle limits are regulatory — cannot be bypassed",
        ],
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="hop_count_excessive",
        pattern=r"(?i)(hop|relay).*(count|limit).*(exceed|too many|max|>\s*[4-7])",
        category=Category.PERFORMANCE,
        cause_template="Message exceeding practical hop limit — each hop degrades reliability",
        suggestions=[
            "Add relay node closer to destination to reduce hop count",
            "Use longer-range preset to skip intermediate hops",
            "Consider infrastructure node placement (elevated, powered)",
            "Maximum reliable hops is typically 3-4 for Meshtastic",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="retransmission_high",
        pattern=r"(?i)(retransmit|retry|resend).*(rate|count|high|excessive|>\s*\d+)",
        category=Category.PERFORMANCE,
        cause_template="High retransmission rate — ACKs not being received",
        suggestions=[
            "Poor link quality causing lost ACKs — improve antenna",
            "Hidden node problem — nodes can hear base but not each other",
            "Reduce traffic to decrease collision probability",
            "Check if destination node is processing messages fast enough",
        ],
        confidence_base=0.8,
    ))

    engine.add_rule(DiagnosticRule(
        name="airtime_limit_approaching",
        pattern=r"(?i)(airtime|air time).*(limit|approach|warning|>\s*[5-9]\d%)",
        category=Category.PERFORMANCE,
        cause_template="Channel airtime limit is approaching — risk of message drops",
        suggestions=[
            "Reduce message rate across all nodes on this channel",
            "Switch some nodes to a different channel",
            "Use shorter messages",
            "Consider faster LoRa preset to reduce per-message airtime",
        ],
        confidence_base=0.8,
    ))
