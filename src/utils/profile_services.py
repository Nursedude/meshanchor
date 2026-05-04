"""Profile-aware service classification — single source of truth.

Phase 5 made `run_health_check` profile-aware (a service relevant for the
active deployment profile is required / optional / not_applicable). This
module hoists that classification out into a reusable helper so the three
sites Phase 5 deferred — `health_score._on_service_event`,
`active_health_probe.create_default_probe`, and `service_menu._bridge_preflight`
— can ask the same question without re-implementing the lookup.

Three callers, three needs:

    is_critical(svc)      — health_score: should a service-down event hurt the
                            overall score? Yes iff service is *required* under
                            the active profile (e.g. rnsd under FULL).
    is_managed(svc)       — active_health_probe: should we even probe this
                            service? Yes iff it's required OR optional under
                            the profile (e.g. meshtasticd under GATEWAY).
    service_role(svc)     — generic: returns "required" / "optional" /
                            "not_applicable", mirroring `ServiceHealth` flags.

All three accept an optional `profile=` for tests / explicit injection. When
`profile is None`, we resolve the active profile via
`deployment_profiles.load_or_detect_profile()` and cache it for the process
lifetime (call `invalidate_cache()` after `save_profile()`). On any resolution
failure we fall back to the legacy hardcoded behaviour — meshtasticd / rnsd
treated as critical, all three (mosquitto too) treated as managed — so a
profile-resolution outage never silently widens or narrows the probe set.
"""

from __future__ import annotations

import logging
import threading
from typing import Literal, Optional

logger = logging.getLogger(__name__)


# Legacy fallback: behaviour Phase 5.5 inherits from before profile-aware
# health was wired through these three sites. Used when profile resolution
# fails so we never crash a daemon over a profile lookup error.
_LEGACY_CRITICAL = frozenset({"meshtasticd", "rnsd"})
_LEGACY_MANAGED = frozenset({"meshtasticd", "rnsd", "mosquitto"})


_ServiceRole = Literal["required", "optional", "not_applicable"]


# Process-lifetime cache. The active profile rarely changes; resolving it
# requires reading ~/.config/meshanchor/deployment.json which is fine at
# startup but we don't want to do it on every EventBus tick.
_cached_profile = None
_cache_lock = threading.Lock()
_cache_set = False


def invalidate_cache() -> None:
    """Drop the cached profile. Call after `save_profile()` so subsequent
    `is_critical` / `is_managed` calls pick up the new selection."""
    global _cached_profile, _cache_set
    with _cache_lock:
        _cached_profile = None
        _cache_set = False


def _active_profile():
    """Resolve + cache the active profile. Returns None on any failure."""
    global _cached_profile, _cache_set
    with _cache_lock:
        if _cache_set:
            return _cached_profile
        try:
            from utils.deployment_profiles import load_or_detect_profile
            _cached_profile = load_or_detect_profile()
        except Exception as e:
            logger.debug("Could not resolve active profile: %s", e)
            _cached_profile = None
        _cache_set = True
        return _cached_profile


def service_role(service_name: str, profile=None) -> _ServiceRole:
    """Classify a service against a profile.

    Returns "required" / "optional" / "not_applicable". When the profile
    can't be resolved, falls back to legacy behaviour (meshtasticd + rnsd
    are required, mosquitto is optional, everything else is not_applicable).
    """
    if profile is None:
        profile = _active_profile()
    if profile is None:
        if service_name in _LEGACY_CRITICAL:
            return "required"
        if service_name in _LEGACY_MANAGED:
            return "optional"
        return "not_applicable"

    required = set(getattr(profile, "required_services", []) or [])
    optional = set(getattr(profile, "optional_services", []) or [])
    if service_name in required:
        return "required"
    if service_name in optional:
        return "optional"
    return "not_applicable"


def is_critical(service_name: str, profile=None) -> bool:
    """True iff the service is *required* by the active profile.

    A service-down event for a critical service should hurt the network
    health score. Under MESHCORE neither meshtasticd nor rnsd is critical;
    under FULL both rnsd and mosquitto are.
    """
    return service_role(service_name, profile) == "required"


def is_managed(service_name: str, profile=None) -> bool:
    """True iff the service is required OR optional under the active profile.

    A managed service is one we want to actively probe. Under MESHCORE
    we don't probe meshtasticd (not_applicable); under GATEWAY we do
    (optional). Mirrors the `ServiceHealth.not_applicable=False` set.
    """
    return service_role(service_name, profile) in ("required", "optional")
