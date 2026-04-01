"""RNS / NomadNet coexistence diagnostic rules for MeshAnchor Diagnostic Engine."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.diagnostic_engine import DiagnosticEngine

from utils.diagnostic_engine import (
    Category,
    DiagnosticRule,
    make_process_check,
    make_service_active_check,
)


def load_rns_coexistence_rules(engine: "DiagnosticEngine") -> None:
    """Load RNS / NomadNet coexistence diagnostic rules."""

    engine.add_rule(DiagnosticRule(
        name="rns_interface_rx_only",
        pattern=(
            r"(?i)(interface|rns).*(rx.?only|no.*tx|"
            r"receive.*only|no.*transmit)"
        ),
        category=Category.CONNECTIVITY,
        cause_template=(
            "RNS interface is receiving packets but not transmitting. "
            "Link establishment (SYN/ACK) is failing, typically because "
            "the shared instance port 37428 is not bound or another "
            "process (NomadNet) is holding it."
        ),
        evidence_checks=[
            make_service_active_check("rnsd"),
        ],
        suggestions=[
            "Check port 37428 owner: sudo ss -ulnp | grep 37428",
            "If NomadNet owns port 37428: stop NomadNet, restart "
            "rnsd, then restart NomadNet",
            "Check share_instance = Yes in Reticulum config",
            "Run rnstatus to see per-interface TX/RX counters",
        ],
        auto_recoverable=False,
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="nomadnet_rnsd_port_conflict",
        pattern=(
            r"(?i)(nomadnet|nomad.?net).*(conflict|port|37428|"
            r"shared.?instance|holding)"
        ),
        category=Category.CONFIGURATION,
        cause_template=(
            "NomadNet and rnsd are competing for the RNS shared "
            "instance port (UDP 37428). Both create their own "
            "Reticulum instance with share_instance=Yes, but only "
            "one can bind the port. Correct startup order: rnsd "
            "first, then NomadNet (as client)."
        ),
        evidence_checks=[
            make_service_active_check("rnsd"),
            make_process_check("nomadnet"),
        ],
        suggestions=[
            "Stop NomadNet: pkill -f nomadnet",
            "Restart rnsd: sudo systemctl restart rnsd",
            "Wait for port 37428 to be listening",
            "Start NomadNet (will connect as client to rnsd)",
            "Correct boot order: rnsd -> NomadNet -> MeshAnchor",
        ],
        auto_recoverable=False,
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="rns_shared_instance_not_listening",
        pattern=(
            r"(?i)(shared.?instance|37428|port).*(not.?listen|"
            r"not.?bound|waiting|unavailable)"
        ),
        category=Category.CONNECTIVITY,
        cause_template=(
            "The RNS shared instance port (UDP 37428) is not bound. "
            "This prevents client applications (NomadNet, rnstatus, "
            "MeshAnchor gateway) from connecting to rnsd. Causes: "
            "share_instance not enabled, blocking interface "
            "preventing rnsd initialization, or NomadNet holding "
            "the port."
        ),
        evidence_checks=[
            make_service_active_check("rnsd"),
            make_process_check("nomadnet"),
        ],
        suggestions=[
            "Check share_instance = Yes in [reticulum] config",
            "Check for NomadNet port conflict: pgrep -f nomadnet",
            "Check for blocking interfaces: RNS > Diagnostics in "
            "MeshAnchor",
            "Restart rnsd: sudo systemctl restart rnsd",
            "Check rnsd logs: sudo journalctl -u rnsd -n 30",
        ],
        auto_recoverable=False,
        confidence_base=0.85,
    ))
