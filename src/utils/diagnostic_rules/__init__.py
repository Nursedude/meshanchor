"""
Diagnostic rules for MeshAnchor Diagnostic Engine.

This package contains all the built-in diagnostic rules for mesh networking,
split into domain-specific modules for maintainability.

Import path is preserved: ``from utils.diagnostic_rules import load_mesh_rules``
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.diagnostic_engine import DiagnosticEngine

from .connectivity import load_connectivity_rules
from .hardware import load_hardware_rules
from .protocol import load_protocol_rules
from .performance import load_performance_rules
from .resource import load_resource_rules
from .configuration import load_configuration_rules
from .security import load_security_rules
from .meshtastic_web import load_meshtastic_web_rules
from .rns_coexistence import load_rns_coexistence_rules


def load_mesh_rules(engine: "DiagnosticEngine") -> None:
    """Load all built-in diagnostic rules for mesh networking."""
    load_connectivity_rules(engine)
    load_hardware_rules(engine)
    load_protocol_rules(engine)
    load_performance_rules(engine)
    load_resource_rules(engine)
    load_configuration_rules(engine)
    load_security_rules(engine)
    load_meshtastic_web_rules(engine)
    load_rns_coexistence_rules(engine)
