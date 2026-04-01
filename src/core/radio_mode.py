"""
RadioMode — Primary radio selection for MeshAnchor.

MeshAnchor is MeshCore-primary. Meshtastic is available as an optional
gateway radio. RadioMode controls which radio is "home" and which is
"foreign" for bridge routing decisions.
"""

import json
import logging
from enum import Enum
from pathlib import Path
from typing import Optional

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


class RadioMode(Enum):
    """Which radio protocol is primary."""
    MESHCORE = "meshcore"       # Primary — MeshCore companion radio
    MESHTASTIC = "meshtastic"   # Secondary — meshtasticd gateway
    DUAL = "dual"               # Both radios active as peers

    @property
    def display_name(self) -> str:
        return {
            RadioMode.MESHCORE: "MeshCore (Primary)",
            RadioMode.MESHTASTIC: "Meshtastic (Gateway)",
            RadioMode.DUAL: "Dual Radio",
        }[self]


# MeshAnchor default — MeshCore is the home radio
DEFAULT_MODE = RadioMode.MESHCORE

_CONFIG_PATH = get_real_user_home() / ".config" / "meshanchor" / "radio_mode.json"


def get_radio_mode() -> RadioMode:
    """Load persisted radio mode, or return DEFAULT_MODE."""
    try:
        if _CONFIG_PATH.exists():
            data = json.loads(_CONFIG_PATH.read_text())
            mode_str = data.get("radio_mode", DEFAULT_MODE.value)
            return RadioMode(mode_str)
    except (json.JSONDecodeError, ValueError, OSError) as e:
        logger.warning("Failed to load radio mode, using default: %s", e)
    return DEFAULT_MODE


def set_radio_mode(mode: RadioMode) -> bool:
    """Persist radio mode selection."""
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps({
            "radio_mode": mode.value,
        }, indent=2) + "\n")
        logger.info("Radio mode set to: %s", mode.display_name)
        return True
    except OSError as e:
        logger.error("Failed to save radio mode: %s", e)
        return False


def detect_available_radios() -> dict:
    """Detect which radio backends are available on this system."""
    from utils.safe_import import safe_import

    _meshcore, has_meshcore = safe_import('meshcore')
    _meshtastic, has_meshtastic = safe_import('meshtastic')

    result = {
        "meshcore_available": has_meshcore,
        "meshtastic_available": has_meshtastic,
        "recommended_mode": DEFAULT_MODE,
    }

    if has_meshcore and has_meshtastic:
        result["recommended_mode"] = RadioMode.DUAL
    elif has_meshcore:
        result["recommended_mode"] = RadioMode.MESHCORE
    elif has_meshtastic:
        result["recommended_mode"] = RadioMode.MESHTASTIC

    return result
