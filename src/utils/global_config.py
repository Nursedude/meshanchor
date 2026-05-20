"""MeshForge Ecosystem Global Configuration Reader

Reads ``~/.config/meshforge/global.ini`` — the shared identity layer
used across MeshForge, MeshAnchor, and related tooling.  The file
belongs to the *ecosystem*, not to any single app; the path intentionally
stays under ``meshforge/`` regardless of which app reads it.

MeshAnchor consumes this as a fallback layer in ``DaemonConfig.load()``:

  dataclass defaults < deployment profile < global.ini
                     < system daemon.yaml < user daemon.yaml < explicit path

Keys returned by :func:`load_global_overrides` match :class:`DaemonConfig`
field names so callers can ``setattr(config, key, value)`` directly.

The file is NEVER required — a missing or malformed global.ini leaves the
app in its per-component defaults without any error.  Malformed INI → log
DEBUG and return empty dict; never raise.  Every NOC service would die at
boot if global.ini got corrupted.

Canonical schema: https://github.com/Nursedude/meshing_around_meshforge/blob/main/docs/global_config.md
NOC reference:    https://github.com/Nursedude/meshforge commit 569a490 src/utils/global_config.py
"""

import configparser
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

GLOBAL_CONFIG_FILENAME = "global.ini"
GLOBAL_CONFIG_DIRNAME = "meshforge"


def global_config_path() -> Path:
    """Canonical path: ``~/.config/meshforge/global.ini``.

    Uses :func:`utils.paths.get_real_user_home` so sudo / systemd never
    redirect to ``/root``.
    """
    return get_real_user_home() / ".config" / GLOBAL_CONFIG_DIRNAME / GLOBAL_CONFIG_FILENAME


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> Optional[bool]:
    """Return None on missing/blank; bool otherwise.

    ``None`` lets the seeding logic distinguish "global said nothing"
    from "global said False" — important because ``mqtt_enabled=False``
    is a real setting we don't want to skip.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s == "":
        return None
    return s in ("true", "yes", "1", "on")


def load_global_overrides(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read ``global.ini`` and return a flat dict of DaemonConfig overrides.

    Keys in the returned dict match :class:`~daemon_config.DaemonConfig`
    field names so callers can ``setattr(config, key, value)`` directly.

    Currently emits keys (only when the corresponding INI value is set):

    - ``mqtt_enabled`` — inferred ``True`` whenever ``[mqtt] broker`` is
      set (an operator who configured a broker wants MQTT on).  Explicit
      false in the per-app ``daemon.yaml`` still wins — global is a
      *fallback* layer, not an override.
    - ``mqtt_broker``, ``mqtt_port`` — direct copies from ``[mqtt]``.

    Future fields from the canonical schema ([node], [region], [paths])
    are not yet consumed by MeshAnchor; this reader only emits keys that
    have a matching destination today, keeping the surface honest.

    Missing or malformed file → empty dict, never raises.
    """
    target = Path(path) if path else global_config_path()
    overrides: Dict[str, Any] = {}

    if not target.exists():
        return overrides

    parser = configparser.ConfigParser()
    try:
        parser.read(str(target))
    except (configparser.Error, OSError, UnicodeDecodeError) as e:
        logger.debug("MeshForge global.ini parse failed (%s): %s", type(e).__name__, e)
        return overrides

    if parser.has_section("mqtt"):
        broker = parser.get("mqtt", "broker", fallback="").strip()
        if broker:
            overrides["mqtt_broker"] = broker
            # An operator who set a broker wants MQTT enabled.  Per-app
            # daemon.yaml can still override this back to False.
            overrides["mqtt_enabled"] = True

        port = _coerce_int(parser.get("mqtt", "port", fallback=""), 0)
        if port:
            overrides["mqtt_port"] = port

        # Explicit mqtt_enabled in [mqtt] (rare — usually inferred from
        # broker presence) takes precedence over the inference above.
        explicit_enabled = _coerce_bool(parser.get("mqtt", "enabled", fallback=None))
        if explicit_enabled is not None:
            overrides["mqtt_enabled"] = explicit_enabled

    if overrides:
        logger.info(
            "MeshForge global config applied %d override(s) from %s",
            len(overrides), target,
        )

    return overrides
