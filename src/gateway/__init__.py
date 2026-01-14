"""
MeshForge Gateway Module
Bridges Reticulum Network Stack (RNS) and Meshtastic networks

Supports three bridge modes:
- message_bridge: Translates messages between RNS/LXMF and Meshtastic
- rns_transport: RNS uses Meshtastic as network transport layer (RNS_Over_Meshtastic)
- mesh_bridge: Bridges two Meshtastic networks with different LoRa presets
"""

from .rns_bridge import RNSMeshtasticBridge
from .node_tracker import UnifiedNodeTracker
from .config import (
    GatewayConfig,
    RNSOverMeshtasticConfig,
    MeshtasticConfig,
    MeshtasticBridgeConfig,
)
from .rns_transport import (
    RNSMeshtasticTransport,
    RNSMeshtasticInterface,
    TransportStats,
    create_rns_transport,
)
from .mesh_bridge import (
    MeshtasticPresetBridge,
    BridgedMeshMessage,
    create_mesh_bridge,
)

__all__ = [
    # RNS-Meshtastic bridge
    'RNSMeshtasticBridge',
    'UnifiedNodeTracker',
    # Configuration
    'GatewayConfig',
    'RNSOverMeshtasticConfig',
    'MeshtasticConfig',
    'MeshtasticBridgeConfig',
    # RNS Transport
    'RNSMeshtasticTransport',
    'RNSMeshtasticInterface',
    'TransportStats',
    'create_rns_transport',
    # Mesh preset bridge
    'MeshtasticPresetBridge',
    'BridgedMeshMessage',
    'create_mesh_bridge',
]
