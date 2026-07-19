"""Role-drift detection — is this box's live systemd unit state diverging from
its declared role?

MeshCore-side port of MeshForge's ``probe_role_drift`` (sister repo, 2026-07-18).
The fleet's role model (``docs/fleet_roles.yaml`` + per-box ``deployment.json``
``role``/``service_overrides``) is converged only when an operator runs
``scripts/provision_role.py --apply`` — between runs, nothing alerts on
divergence. This makes the converge SSOT's own dry-run plan a continuously-
checkable signal.

Design split (matches MeshAnchor's watchdog idiom): this module answers the
POINT-IN-TIME question "is there drift right now, and what?" and returns a
reason string (or ``None``). The 2-cycle hysteresis that turns a persistent
divergence into a ``role_drift`` blackout lives in ``monitoring.fleet_watchdog``
(same shape as its ``daemon_dead`` streak) — role catalog (git) and unit state
(converge/restarts) deploy independently, so a single cycle can catch a
deploy window.

Drift = any plan action whose verb is enable/disable/mask (the box would change
under converge) OR a blocking warning (required unit not installed; a waiver
missing its required ``reason``). Documented, reasoned overrides are honored
(reported as non-blocking advisories that do NOT count as drift). A role missing
from the catalog counts as drift.

Returns ``None`` (no drift / not applicable) when the box declares no role, when
the tool/catalog can't be loaded (indeterminate — never false-alarm), or when
the plan is clean.
"""
from __future__ import annotations

import os
from typing import List, Optional, Tuple

DEFAULT_MESHANCHOR_ROOT = "/opt/meshanchor"

# plan() verbs that mean "the box would change under converge" = real drift.
_ROLE_DRIFT_VERBS = ("enable", "disable", "mask")


def _load_provision_role(meshanchor_root: str):
    """importlib-load ``scripts/provision_role.py`` (the converge SSOT).

    Returns the module, or ``None`` when it can't be loaded (indeterminate).
    """
    try:
        import importlib.util
        import sys
        script = os.path.join(meshanchor_root, "scripts", "provision_role.py")
        spec = importlib.util.spec_from_file_location("ma_provision_role", script)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod  # register before exec (py3.12+ @dataclass eval)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _plan_role_actions(mod, role: str, overrides: dict):
    """Return provision_role.plan() actions for this box's effective role.

    Raises KeyError for a role missing from the catalog (a REAL mismatch the
    caller counts as drift). Returns ``None`` when the catalog can't be loaded.
    """
    try:
        catalog = mod.load_roles(mod.DEFAULT_ROLES_FILE)
    except Exception:
        return None
    role_def = mod.resolve_role(catalog, role)  # KeyError -> unknown role
    return mod.plan(role_def, overrides)


def collect_drift_items(
    *,
    meshanchor_root: str = DEFAULT_MESHANCHOR_ROOT,
    deployment: Optional[Tuple[Optional[str], dict]] = None,
) -> Optional[List[str]]:
    """Return the list of drift items (empty list = converged), or ``None`` when
    the check is not applicable / indeterminate (no role, tool/catalog
    unavailable, tool error).

    ``deployment`` is an optional ``(role, overrides)`` tuple for testing; when
    omitted it is read from ``deployment.json`` via the converge SSOT.
    """
    mod = _load_provision_role(meshanchor_root)
    if mod is None:
        return None  # tool unavailable -> indeterminate

    if deployment is None:
        role = mod.read_role()
        overrides = mod.read_overrides()
    else:
        role, overrides = deployment
    if not role:
        return None  # box not role-declared -> not applicable

    unknown_role = False
    try:
        actions = _plan_role_actions(mod, role, overrides or {})
    except KeyError:
        unknown_role = True
        actions = []
    except Exception:
        return None  # tool error -> indeterminate

    if actions is None and not unknown_role:
        return None  # catalog unavailable -> indeterminate

    if unknown_role:
        return [f"role '{role}' not in the fleet_roles.yaml catalog"]

    items: List[str] = []
    for a in actions:
        verb = getattr(a, "verb", "")
        if verb in _ROLE_DRIFT_VERBS or (
            verb == "warn" and getattr(a, "required", False)
        ):
            items.append(
                f"{getattr(a, 'item', '?')}: "
                f"{getattr(a, 'current', '?')} -> {getattr(a, 'desired', '?')}"
            )
    return items


def evaluate_role_drift(
    *,
    meshanchor_root: str = DEFAULT_MESHANCHOR_ROOT,
    deployment: Optional[Tuple[Optional[str], dict]] = None,
) -> Optional[str]:
    """Point-in-time role-drift verdict for the watchdog.

    Returns a human-readable reason string when the box diverges from its
    declared role, else ``None`` (converged, not role-declared, or
    indeterminate). No hysteresis here — the caller debounces.
    """
    items = collect_drift_items(meshanchor_root=meshanchor_root, deployment=deployment)
    if not items:
        return None

    role = deployment[0] if deployment else None
    if role is None:
        mod = _load_provision_role(meshanchor_root)
        role = (mod.read_role() if mod else None) or "?"
    shown = "; ".join(items[:4]) + (f" (+{len(items) - 4} more)" if len(items) > 4 else "")
    return (
        f"live unit state diverges from declared role '{role}' "
        f"({len(items)} item(s)): {shown} | documented service_overrides are "
        f"honored (not drift). Review: python3 scripts/provision_role.py "
        f"(dry-run); converge with sudo python3 scripts/provision_role.py --apply, "
        f"or correct the declared role."
    )
