"""
MeshAnchor Deployment Profiles

Defines 5 deployment scenarios so users can run MeshAnchor in their
chosen configuration without irrelevant dependencies blocking them.

Profiles:
    radio_maps  - meshtasticd + coverage mapping, RF tools
    monitor     - MQTT packet analysis, no radio needed
    meshcore    - MeshCore companion radio integration
    gateway     - Full Meshtastic <> RNS bridge
    full        - Everything including MQTT broker

Usage:
    from utils.deployment_profiles import load_or_detect_profile

    profile = load_or_detect_profile()
    print(profile.display_name)
    print(profile.validate())
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any

from utils.paths import get_real_user_home
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Service checker (optional — profile module should work standalone)
_check_service, _ServiceState, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_service', 'ServiceState'
)


class ProfileName(Enum):
    """Deployment profile identifiers."""
    RADIO_MAPS = "radio_maps"
    MONITOR = "monitor"
    MESHCORE = "meshcore"
    GATEWAY = "gateway"
    FULL = "full"


@dataclass
class ProfileDefinition:
    """Definition of a deployment profile."""
    name: ProfileName
    display_name: str
    description: str
    required_services: List[str]
    optional_services: List[str]
    required_packages: List[str]
    optional_packages: List[str]
    feature_flags: Dict[str, bool]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON persistence."""
        return {
            'name': self.name.value,
            'display_name': self.display_name,
            'description': self.description,
        }


@dataclass
class ProfileHealth:
    """Result of validating a profile against the current system."""
    profile: ProfileDefinition
    missing_services: List[str] = field(default_factory=list)
    missing_packages: List[str] = field(default_factory=list)
    available_services: List[str] = field(default_factory=list)
    available_packages: List[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """All required services and packages present."""
        return not self.missing_services and not self.missing_packages

    @property
    def summary(self) -> str:
        """Human-readable health summary."""
        if self.ready:
            return f"{self.profile.display_name}: Ready"
        parts = []
        if self.missing_services:
            parts.append(f"services: {', '.join(self.missing_services)}")
        if self.missing_packages:
            parts.append(f"packages: {', '.join(self.missing_packages)}")
        return f"{self.profile.display_name}: Missing {'; '.join(parts)}"


# ============================================================================
# Profile Definitions
# ============================================================================

PROFILES: Dict[ProfileName, ProfileDefinition] = {
    # MeshAnchor default: MeshCore companion radio + RF tools
    ProfileName.MESHCORE: ProfileDefinition(
        name=ProfileName.MESHCORE,
        display_name="MeshCore",
        description="MeshCore companion radio — primary MeshAnchor profile",
        required_services=[],
        optional_services=[],
        required_packages=["rich", "yaml", "requests"],
        optional_packages=["psutil", "meshcore"],
        feature_flags={
            "meshtastic": False,
            "meshcore": True,
            "rns": False,
            "gateway": False,
            "mqtt": False,
            "maps": False,
            "tactical": False,
        },
    ),
    ProfileName.RADIO_MAPS: ProfileDefinition(
        name=ProfileName.RADIO_MAPS,
        display_name="MeshCore + Maps",
        description="MeshCore radio with coverage mapping",
        required_services=[],
        optional_services=[],
        required_packages=["rich", "yaml", "requests", "folium"],
        optional_packages=["psutil", "distro", "meshcore"],
        feature_flags={
            "meshtastic": False,
            "meshcore": True,
            "rns": False,
            "gateway": False,
            "mqtt": False,
            "maps": True,
            "tactical": False,
        },
    ),
    ProfileName.MONITOR: ProfileDefinition(
        name=ProfileName.MONITOR,
        display_name="Monitor",
        description="MQTT packet analysis and traffic inspection (no radio required)",
        required_services=[],
        optional_services=["mosquitto"],
        required_packages=["rich", "yaml", "requests", "paho"],
        optional_packages=["psutil", "websockets"],
        feature_flags={
            "meshtastic": False,
            "meshcore": False,
            "rns": False,
            "gateway": False,
            "mqtt": True,
            "maps": False,
            "tactical": False,
        },
    ),
    ProfileName.GATEWAY: ProfileDefinition(
        name=ProfileName.GATEWAY,
        display_name="Gateway Bridge",
        description="MeshCore <> Meshtastic/RNS bridge with message routing",
        required_services=[],
        optional_services=["meshtasticd", "rnsd", "mosquitto"],
        required_packages=["rich", "yaml", "requests", "paho"],
        optional_packages=["RNS", "LXMF", "websockets", "psutil", "folium", "meshcore"],
        feature_flags={
            "meshtastic": True,
            "meshcore": True,
            "rns": True,
            "gateway": True,
            "mqtt": True,
            "maps": True,
            "tactical": True,
        },
    ),
    ProfileName.FULL: ProfileDefinition(
        name=ProfileName.FULL,
        display_name="Full Stack",
        description="All features enabled — MeshCore + Meshtastic + RNS",
        required_services=["rnsd", "mosquitto"],
        optional_services=["meshtasticd"],
        required_packages=[
            "rich", "yaml", "requests", "RNS", "LXMF", "paho",
            "folium", "websockets", "psutil", "distro",
        ],
        optional_packages=["meshcore"],
        feature_flags={
            "meshtastic": True,
            "meshcore": True,
            "rns": True,
            "gateway": True,
            "mqtt": True,
            "maps": True,
            "tactical": True,
        },
    ),
}


# ============================================================================
# Package Detection
# ============================================================================

# Map import names to the actual module to try importing
_PACKAGE_IMPORT_MAP = {
    "rich": "rich",
    "yaml": "yaml",
    "requests": "requests",
    "folium": "folium",
    "RNS": "RNS",
    "LXMF": "LXMF",
    "paho": "paho.mqtt.client",
    "websockets": "websockets",
    "psutil": "psutil",
    "distro": "distro",
    "meshcore": "meshcore",
}


def _check_package(name: str) -> bool:
    """Check if a Python package is importable."""
    import_name = _PACKAGE_IMPORT_MAP.get(name, name)
    _, available = safe_import(import_name)
    return available


def _check_service_available(name: str) -> bool:
    """Check if a system service is running."""
    if not _HAS_SERVICE_CHECK:
        logger.debug("service_check not available, skipping service detection")
        return False
    try:
        status = _check_service(name)
        return status.available
    except Exception as e:
        logger.debug("Service check failed for %s: %s", name, e)
        return False


# ============================================================================
# Profile Validation
# ============================================================================

def validate_profile(profile: ProfileDefinition) -> ProfileHealth:
    """Validate a profile against the current system state.

    Checks required services and packages, returns a ProfileHealth
    indicating what is present vs missing.
    """
    health = ProfileHealth(profile=profile)

    for svc in profile.required_services:
        if _check_service_available(svc):
            health.available_services.append(svc)
        else:
            health.missing_services.append(svc)

    for pkg in profile.required_packages:
        if _check_package(pkg):
            health.available_packages.append(pkg)
        else:
            health.missing_packages.append(pkg)

    return health


# ============================================================================
# Profile Detection
# ============================================================================

def detect_profile() -> ProfileDefinition:
    """Auto-detect the best profile based on running services and installed packages.

    Detection priority (MeshAnchor — MeshCore-primary):
    1. Full — all services running (meshtasticd + rnsd + mosquitto)
    2. Gateway — meshtasticd or rnsd available for bridging
    3. MeshCore + Maps — folium available
    4. Monitor — paho-mqtt available, no radio
    5. MeshCore — default (MeshCore companion radio)

    Returns the best-fit profile, defaulting to meshcore.
    """
    has_meshtasticd = _check_service_available("meshtasticd")
    has_rnsd = _check_service_available("rnsd")
    has_mosquitto = _check_service_available("mosquitto")

    # Full stack: rnsd + mosquitto (meshtasticd optional)
    if has_rnsd and has_mosquitto:
        logger.info("Auto-detected profile: full (rnsd + mosquitto running)")
        return PROFILES[ProfileName.FULL]

    # Gateway: meshtasticd or rnsd available for bridging
    if has_meshtasticd or has_rnsd:
        logger.info("Auto-detected profile: gateway (bridge services available)")
        return PROFILES[ProfileName.GATEWAY]

    # MeshCore + Maps: folium available
    if _check_package("folium"):
        logger.info("Auto-detected profile: radio_maps (MeshCore + maps)")
        return PROFILES[ProfileName.RADIO_MAPS]

    # Monitor: paho available, no radio services
    if _check_package("paho") and not _check_package("meshcore"):
        logger.info("Auto-detected profile: monitor (no radio, MQTT available)")
        return PROFILES[ProfileName.MONITOR]

    # Default: MeshCore companion radio
    logger.info("Auto-detected profile: meshcore (default)")
    return PROFILES[ProfileName.MESHCORE]


# ============================================================================
# Profile Persistence
# ============================================================================

_PROFILE_PATH = get_real_user_home() / ".config" / "meshanchor" / "deployment.json"


def save_profile(profile: ProfileDefinition) -> bool:
    """Save profile selection to disk."""
    try:
        _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"profile": profile.name.value}
        with open(_PROFILE_PATH, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info("Saved deployment profile: %s", profile.display_name)
        # Drop any cached profile in profile_services so subsequent
        # is_critical / is_managed calls reflect the new selection.
        try:
            from utils.profile_services import invalidate_cache
            invalidate_cache()
        except Exception as e:
            logger.debug("profile_services cache invalidation failed: %s", e)
        return True
    except (IOError, OSError) as e:
        logger.warning("Failed to save deployment profile: %s", e)
        return False


def load_profile() -> Optional[ProfileDefinition]:
    """Load saved profile from disk. Returns None if no saved profile."""
    if not _PROFILE_PATH.exists():
        return None
    try:
        with open(_PROFILE_PATH, 'r') as f:
            data = json.load(f)
        name_str = data.get("profile")
        if not name_str:
            return None
        profile_name = ProfileName(name_str)
        logger.info("Loaded saved profile: %s", profile_name.value)
        return PROFILES[profile_name]
    except (json.JSONDecodeError, IOError, ValueError, KeyError) as e:
        logger.warning("Failed to load deployment profile: %s", e)
        return None


def load_or_detect_profile() -> ProfileDefinition:
    """Load saved profile, or auto-detect if none saved."""
    saved = load_profile()
    if saved is not None:
        return saved
    return detect_profile()


def get_profile_by_name(name: str) -> Optional[ProfileDefinition]:
    """Look up a profile by string name. Returns None if not found."""
    try:
        return PROFILES[ProfileName(name)]
    except (ValueError, KeyError):
        return None


def list_profiles() -> List[ProfileDefinition]:
    """Return all available profiles in display order."""
    return [
        PROFILES[ProfileName.RADIO_MAPS],
        PROFILES[ProfileName.MONITOR],
        PROFILES[ProfileName.MESHCORE],
        PROFILES[ProfileName.GATEWAY],
        PROFILES[ProfileName.FULL],
    ]
