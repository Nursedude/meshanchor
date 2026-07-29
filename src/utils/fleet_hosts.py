"""MeshAnchor fleet_hosts membership — THE Python resolver.

Mirror of ``scripts/lib/fleet_hosts.sh`` (the meshanchor namespace); the two
are the only permitted implementations of the resolution chain and are
pinned to each other by ``tests/test_fleet_hosts_resolver.py`` (same fixture
tree fed to both, same answer required).

Born 2026-07-29 closing the WS-A port artifact: ``mini_dudeai.rollup`` (and
through it the daemon's ``--preset auto``) resolved the MESHFORGE-namespaced
list verbatim from the MF twin — on meshanchor-server that file does not
exist, so the fleet pane silently degraded to a 1-box view, and on the one
box that HAS the meshforge list (the manager) the pane would have swept the
wrong fleet and ``auto`` would have crashed on a preset name MA does not
ship. The collector-of-record for /api/fleet/truth is untouched — it reads
``fleet.json`` peers by documented design, not this file.

Resolution order::

    $MESHANCHOR_FLEET_HOSTS   - AUTHORITATIVE when set: a missing/unreadable
    $FLEET_HOSTS                override yields NO list rather than falling
                                through to the box's real config (a degraded
                                state must not read as a valid value;
                                FLEET_HOSTS is the legacy alias
                                lab_traffic_rollup documented)
    ~/.config/meshanchor/fleet_hosts
    /etc/meshanchor/fleet_hosts

Deliberate divergences from the MF twin's ``utils/fleet_hosts.py``: the
meshanchor namespace, and NO per-repo ``fleet_hosts.<basename>`` tier (no MA
consumer scopes by repo; an unused tier is speculative surface).

Home is the REAL user's home (sudo-safe) unless an ``env`` mapping is
injected — tests pass ``env={"HOME": ...}`` and ``etc_dir=`` so no assertion
ever depends on the machine running the suite.

File format: hosts separated by whitespace/newlines; ``#`` starts a comment
anywhere on the line, so ``"moc1  # retired"`` parses as host ``moc1``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Mapping, Optional

DEFAULT_ETC_DIR = "/etc/meshanchor"
#: First SET (non-empty) key wins and is authoritative.
ENV_OVERRIDE_KEYS = ("MESHANCHOR_FLEET_HOSTS", "FLEET_HOSTS")


def parse_fleet_hosts_text(text: str) -> List[str]:
    """Hosts from a fleet_hosts document: ``#``-to-EOL stripped, then
    whitespace-split — identical to the shell lib's sed|tr|grep pipeline."""
    hosts: List[str] = []
    for raw in text.splitlines():
        hosts.extend(raw.split("#", 1)[0].split())
    return hosts


def _readable_file(p: Path) -> bool:
    try:
        return p.is_file() and os.access(p, os.R_OK)
    except OSError:
        return False


def resolve_fleet_hosts_file(
    *,
    env: Optional[Mapping[str, str]] = None,
    etc_dir: str = DEFAULT_ETC_DIR,
) -> Optional[Path]:
    """The file that wins the resolution order, or ``None`` if no list exists
    (including a SET-but-unresolvable env override — authoritative, no
    fall-through)."""
    env_map: Mapping[str, str] = os.environ if env is None else env
    for key in ENV_OVERRIDE_KEYS:
        val = env_map.get(key)
        if val:
            p = Path(val)
            return p if _readable_file(p) else None
    if env is None:
        from utils.paths import get_real_user_home
        home: Optional[Path] = get_real_user_home()
    else:
        home = Path(env["HOME"]) if env.get("HOME") else None
    candidates: List[Path] = []
    if home:
        candidates.append(home / ".config" / "meshanchor" / "fleet_hosts")
    candidates.append(Path(etc_dir) / "fleet_hosts")
    for c in candidates:
        if _readable_file(c):
            return c
    return None


def resolve_fleet_hosts(
    *,
    env: Optional[Mapping[str, str]] = None,
    etc_dir: str = DEFAULT_ETC_DIR,
) -> List[str]:
    """The host list, or ``[]`` when no list resolves."""
    f = resolve_fleet_hosts_file(env=env, etc_dir=etc_dir)
    if f is None:
        return []
    try:
        return parse_fleet_hosts_text(f.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
