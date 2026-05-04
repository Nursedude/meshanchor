"""Persisted endpoint config for the meshforge-maps :8808 service.

Phase 6.3 follow-up to the Phase 6 scaffold. The Phase 6 client and TUI
handler hardcoded ``localhost:8808``; this module reads host / port /
timeout from a ``meshforge_maps`` :class:`SettingsManager` so the endpoint
can be retargeted at a meshforge-maps instance running on another host on
the LAN (or a non-default port) without code changes.

Defaults match the Phase 6 hardcoded values exactly — existing localhost
deployments keep working without a settings file. Validation happens
inside :func:`save_maps_config` (and :meth:`MapsConfig.validate`) — bad
values raise :class:`MapsConfigError` rather than silently writing junk.

Usage::

    from utils.meshforge_maps_config import load_maps_config, save_maps_config

    cfg = load_maps_config()           # always returns a usable MapsConfig
    client = cfg.build_client()        # ready-to-probe MeshforgeMapsClient

    save_maps_config(host="maps.lan", port=8808, timeout=5.0)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from utils.common import SettingsManager
from utils.meshforge_maps_client import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    MeshforgeMapsClient,
)

logger = logging.getLogger(__name__)


SETTINGS_NAME = "meshforge_maps"
DEFAULTS = {
    "host": DEFAULT_HOST,
    "port": DEFAULT_PORT,
    "timeout": DEFAULT_TIMEOUT,
}


class MapsConfigError(ValueError):
    """Raised when a host / port / timeout value is rejected by validation."""


@dataclass(frozen=True)
class MapsConfig:
    """Resolved meshforge-maps endpoint config.

    Frozen so handler code can pass it around without worrying about
    mutation; rebuild via :func:`load_maps_config` after a save.
    """

    host: str
    port: int
    timeout: float

    def build_client(self) -> MeshforgeMapsClient:
        """Construct a probe client with this config's host/port/timeout."""
        return MeshforgeMapsClient(
            host=self.host, port=self.port, timeout=self.timeout
        )

    def validate(self) -> None:
        """Raise :class:`MapsConfigError` if any field is out of range."""
        _validate_host(self.host)
        _validate_port(self.port)
        _validate_timeout(self.timeout)


def load_maps_config() -> MapsConfig:
    """Read settings from disk; fall back to defaults on any failure.

    Never raises. Bad fields on disk are logged and the default for that
    field is used instead — the user can fix the value via the TUI without
    being locked out by a corrupted on-disk override.
    """
    try:
        sm = SettingsManager(SETTINGS_NAME, defaults=DEFAULTS)
    except Exception as e:  # pragma: no cover - SettingsManager is robust
        logger.warning("meshforge_maps settings load failed: %s; using defaults", e)
        return _defaults_config()

    host = _safe_host(sm.get("host"))
    port = _safe_port(sm.get("port"))
    timeout = _safe_timeout(sm.get("timeout"))
    return MapsConfig(host=host, port=port, timeout=timeout)


def save_maps_config(
    host: Optional[str] = None,
    port: Optional[int] = None,
    timeout: Optional[float] = None,
) -> MapsConfig:
    """Validate + persist any subset of host / port / timeout.

    Unspecified fields keep their current on-disk value (or the default if
    no file exists yet). Raises :class:`MapsConfigError` on bad input.
    Returns the fully-resolved config that was written.
    """
    sm = SettingsManager(SETTINGS_NAME, defaults=DEFAULTS)
    if host is not None:
        _validate_host(host)
        sm.set("host", host)
    if port is not None:
        _validate_port(port)
        sm.set("port", int(port))
    if timeout is not None:
        _validate_timeout(timeout)
        sm.set("timeout", float(timeout))
    sm.save()
    return MapsConfig(
        host=_safe_host(sm.get("host")),
        port=_safe_port(sm.get("port")),
        timeout=_safe_timeout(sm.get("timeout")),
    )


def reset_maps_config() -> MapsConfig:
    """Reset all fields back to the Phase 6 defaults and save."""
    sm = SettingsManager(SETTINGS_NAME, defaults=DEFAULTS)
    sm.reset()
    return _defaults_config()


# ─────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────


def _validate_host(host) -> None:
    if not isinstance(host, str) or not host.strip():
        raise MapsConfigError("host must be a non-empty string")
    if len(host) > 253:
        raise MapsConfigError("host is too long (max 253 chars)")
    if host.startswith("-"):
        raise MapsConfigError("host must not start with '-'")
    # Permissive — allow IPv4, IPv6 (with brackets stripped), DNS names.
    # The probe will surface unreachable hosts at runtime.
    for ch in host:
        if not (ch.isalnum() or ch in ".-:_"):
            raise MapsConfigError(f"host contains invalid character: {ch!r}")


def _validate_port(port) -> None:
    try:
        p = int(port)
    except (TypeError, ValueError):
        raise MapsConfigError("port must be an integer 1-65535")
    if not 1 <= p <= 65535:
        raise MapsConfigError("port must be in range 1-65535")


def _validate_timeout(timeout) -> None:
    try:
        t = float(timeout)
    except (TypeError, ValueError):
        raise MapsConfigError("timeout must be a number > 0")
    if not (0 < t <= 60):
        raise MapsConfigError("timeout must be > 0 and <= 60 seconds")


# ─────────────────────────────────────────────────────────────────────
# Coercion helpers — read path is forgiving, write path is strict
# ─────────────────────────────────────────────────────────────────────


def _safe_host(value) -> str:
    try:
        _validate_host(value)
        return value
    except MapsConfigError:
        logger.warning(
            "meshforge_maps host %r invalid on disk; using default %r",
            value, DEFAULT_HOST,
        )
        return DEFAULT_HOST


def _safe_port(value) -> int:
    try:
        _validate_port(value)
        return int(value)
    except MapsConfigError:
        logger.warning(
            "meshforge_maps port %r invalid on disk; using default %d",
            value, DEFAULT_PORT,
        )
        return DEFAULT_PORT


def _safe_timeout(value) -> float:
    try:
        _validate_timeout(value)
        return float(value)
    except MapsConfigError:
        logger.warning(
            "meshforge_maps timeout %r invalid on disk; using default %s",
            value, DEFAULT_TIMEOUT,
        )
        return DEFAULT_TIMEOUT


def _defaults_config() -> MapsConfig:
    return MapsConfig(
        host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT,
    )
