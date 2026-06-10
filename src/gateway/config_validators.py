"""Gateway configuration validators.

Code-motion extraction from ``gateway/config.py`` (1,500-line rule):
:class:`ConfigValidationError` plus the pure ``validate_*`` field
validators used by :meth:`GatewayConfig.validate`. Import them from
``gateway.config`` (the hub) — that module re-exports every name here,
so external import paths are unchanged. Zero behavior change.
"""

import re
from typing import Optional, List


# =============================================================================
# CONFIGURATION VALIDATION
# =============================================================================

class ConfigValidationError:
    """Represents a configuration validation error or warning."""
    def __init__(self, field: str, message: str, severity: str = "error"):
        self.field = field
        self.message = message
        self.severity = severity  # "error", "warning", "info"

    def __str__(self):
        return f"[{self.severity.upper()}] {self.field}: {self.message}"


def validate_regex(pattern: str, field_name: str) -> Optional[ConfigValidationError]:
    """Validate that a string is a valid regex pattern."""
    if not pattern:
        return None  # Empty is valid (means "match all")
    try:
        re.compile(pattern)
        return None
    except re.error as e:
        return ConfigValidationError(field_name, f"Invalid regex: {e}")


def validate_port(port: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate that a port number is in valid range."""
    if not 1 <= port <= 65535:
        return ConfigValidationError(field_name, f"Port {port} out of range (1-65535)")
    return None


def validate_hop_limit(hop_limit: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate hop limit is in Meshtastic range."""
    if not 1 <= hop_limit <= 7:
        return ConfigValidationError(field_name, f"Hop limit {hop_limit} out of range (1-7)")
    return None


def validate_data_speed(speed: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate data speed preset."""
    if not 0 <= speed <= 8:
        return ConfigValidationError(field_name, f"Data speed {speed} out of range (0-8)")
    return None


def validate_bridge_mode(mode: str, field_name: str) -> Optional[ConfigValidationError]:
    """Validate bridge mode."""
    valid_modes = [
        "mqtt_bridge", "message_bridge", "rns_transport", "mesh_bridge",
        "meshcore_bridge", "tri_bridge",
    ]
    if mode not in valid_modes:
        return ConfigValidationError(field_name, f"Invalid bridge mode '{mode}'. Valid: {valid_modes}")
    return None


def validate_meshcore_connection(conn_type: str, field_name: str) -> Optional[ConfigValidationError]:
    """Validate MeshCore connection type."""
    valid = ["serial", "tcp", "ble"]
    if conn_type not in valid:
        return ConfigValidationError(field_name, f"Invalid connection type '{conn_type}'. Valid: {valid}")
    return None


def validate_direction(direction: str, field_name: str) -> Optional[ConfigValidationError]:
    """Validate routing direction."""
    valid = [
        "bidirectional", "mesh_to_rns", "rns_to_mesh",
        "primary_to_secondary", "secondary_to_primary",
        "mesh_to_meshcore", "meshcore_to_mesh",
        "rns_to_meshcore", "meshcore_to_rns",
        "all_to_all",
    ]
    if direction not in valid:
        return ConfigValidationError(field_name, f"Invalid direction '{direction}'. Valid: {valid}")
    return None


def validate_dedup_window(seconds: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate dedup window is reasonable."""
    if seconds < 10:
        return ConfigValidationError(
            field_name,
            f"Dedup window {seconds}s is very short (may miss duplicates)",
            severity="warning"
        )
    if seconds > 600:
        return ConfigValidationError(
            field_name,
            f"Dedup window {seconds}s is very long (may block legitimate messages)",
            severity="warning"
        )
    return None


def validate_speed_hop_combination(speed: int, hop_limit: int) -> Optional[ConfigValidationError]:
    """Check for incompatible speed/hop combinations."""
    # High speed + high hops = likely packet loss due to timing
    if speed >= 7 and hop_limit >= 5:
        return ConfigValidationError(
            "rns_transport",
            f"Speed {speed} with hop_limit {hop_limit} may cause reliability issues (fast speed + many hops)",
            severity="warning"
        )
    # Low speed + low hops = underutilizing range
    if speed <= 2 and hop_limit <= 2:
        return ConfigValidationError(
            "rns_transport",
            f"Speed {speed} with hop_limit {hop_limit} may underutilize range capability",
            severity="info"
        )
    return None


def validate_log_level(level: str, field_name: str) -> Optional[ConfigValidationError]:
    """Validate that log_level is a standard Python logging level."""
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if level.upper() not in valid_levels:
        return ConfigValidationError(
            field_name, f"Invalid log level '{level}'. Valid: {valid_levels}")
    return None


def validate_channel(channel: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate Meshtastic channel index (0-7)."""
    if not 0 <= channel <= 7:
        return ConfigValidationError(
            field_name, f"Channel {channel} out of range (0-7)")
    return None


def validate_channel_list(channels, field_name: str) -> List[ConfigValidationError]:
    """Validate a channel allow-list: a list of Meshtastic channel indexes.

    Returns a list of errors (empty = valid). Rejects non-list values and
    non-integer entries loudly — a typo'd allow-list silently bridging the
    wrong channels is exactly the failure shape this feature exists to stop.
    Note: bool is an int subclass in Python, so True/False are rejected
    explicitly.
    """
    if not isinstance(channels, list):
        return [ConfigValidationError(
            field_name,
            f"Must be a list of channel indexes 0-7, got {type(channels).__name__}")]
    errors = []
    for i, ch in enumerate(channels):
        if isinstance(ch, bool) or not isinstance(ch, int):
            errors.append(ConfigValidationError(
                f"{field_name}[{i}]",
                f"Channel index must be an integer 0-7, got {ch!r}"))
            continue
        err = validate_channel(ch, f"{field_name}[{i}]")
        if err:
            errors.append(err)
    return errors


def validate_baud_rate(baud: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate serial baud rate is a standard value."""
    standard_rates = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
    if baud not in standard_rates:
        return ConfigValidationError(
            field_name,
            f"Non-standard baud rate {baud}. Standard: {standard_rates}",
            severity="warning"
        )
    return None


def validate_position_precision(precision: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate telemetry position precision (decimal places)."""
    if not 0 <= precision <= 10:
        return ConfigValidationError(
            field_name, f"Position precision {precision} out of range (0-10)")
    return None


def validate_update_interval(interval: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate telemetry update interval is reasonable."""
    if interval < 10:
        return ConfigValidationError(
            field_name,
            f"Update interval {interval}s is very short (min recommended: 10s)",
            severity="warning"
        )
    if interval > 86400:
        return ConfigValidationError(
            field_name,
            f"Update interval {interval}s exceeds 24 hours",
            severity="warning"
        )
    return None


def validate_hostname_config(host: str, field_name: str) -> Optional[ConfigValidationError]:
    """Validate hostname using shared validator from utils.validation."""
    from utils.validation import validate_hostname as _validate_host
    if not host:
        return ConfigValidationError(
            field_name, "Hostname is empty", severity="warning")
    if not _validate_host(host):
        return ConfigValidationError(
            field_name, f"Invalid hostname/IP: '{host}'")
    return None
