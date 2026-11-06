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

A tier that is ABSENT passes to the next one. A tier that is PRESENT but
cannot be read — permission, a directory, a dangling symlink — or cannot be
CHECKED (a parent directory is not searchable) raises
:class:`FleetHostsUnreadable` naming the path, and so does a read that fails
after resolution. Ported 2026-11-05 from MF r7e #2 (a7d198f0) + r7f
(d4fb041f); operator: "quietly failing is not an answer". Falling through
handed the caller the NEXT tier's list — a different fleet, reported as the
fleet.

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
import stat
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

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


class FleetHostsUnreadable(OSError):
    """A fleet host list at ``path`` is present but unusable, or cannot be
    checked. An ``OSError`` so an existing ``except OSError`` still refuses;
    every caller must turn it into its own refusal / UNKNOWN, never "no
    fleet". Same class, same words as the MF twin."""

    def __init__(self, path: Path, reason: str):
        self.path = Path(path)
        self.reason = reason
        super().__init__(
            "fleet host list %s is unreadable (%s) - refusing to fall "
            "through to another list" % (self.path, reason))


#: A tier we cannot prove absent. Shared wording with the shell twin.
UNCHECKABLE = "cannot be checked: a parent directory is not searchable"


def _usable(p: Path) -> bool:
    """True = a readable regular file; False = provably absent (ENOENT, or a
    non-directory where a parent should be); raises when present but unusable
    OR uncheckable. Explicit ``lstat``, not ``Path.is_file`` (whose EACCES
    handling differs by Python version) — mirrors MF r7f F2."""
    try:
        st = os.lstat(p)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except PermissionError as exc:
        raise FleetHostsUnreadable(p, UNCHECKABLE) from exc
    except (OSError, ValueError) as exc:
        raise FleetHostsUnreadable(p, getattr(exc, "strerror", None) or str(exc)) from exc
    if stat.S_ISLNK(st.st_mode):
        try:
            st = os.stat(p)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise FleetHostsUnreadable(p, "dangling symlink") from exc
        except OSError as exc:
            raise FleetHostsUnreadable(p, "symlink target " + UNCHECKABLE) from exc
    if stat.S_ISDIR(st.st_mode):
        raise FleetHostsUnreadable(p, "a directory, not a file")
    if not stat.S_ISREG(st.st_mode):
        raise FleetHostsUnreadable(p, "not a regular file")
    if not os.access(p, os.R_OK):
        raise FleetHostsUnreadable(p, "permission denied")
    return True


def resolve_fleet_hosts_file(
    *,
    env: Optional[Mapping[str, str]] = None,
    etc_dir: str = DEFAULT_ETC_DIR,
) -> Optional[Path]:
    """The file that wins the resolution order, or ``None`` if no list exists
    (including a SET-but-ABSENT env override — authoritative, no
    fall-through). Raises :class:`FleetHostsUnreadable` when the winning
    candidate — or any tier above it — is present but unusable/uncheckable."""
    env_map: Mapping[str, str] = os.environ if env is None else env
    for key in ENV_OVERRIDE_KEYS:
        val = env_map.get(key)
        if val:
            p = Path(val)
            return p if _usable(p) else None
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
        if _usable(c):
            return c
    return None


def resolve_fleet_hosts(
    *,
    env: Optional[Mapping[str, str]] = None,
    etc_dir: str = DEFAULT_ETC_DIR,
) -> List[str]:
    """The host list, or ``[]`` when no list resolves. An unreadable list
    RAISES :class:`FleetHostsUnreadable` — never ``[]``."""
    f = resolve_fleet_hosts_file(env=env, etc_dir=etc_dir)
    if f is None:
        return []
    try:
        return parse_fleet_hosts_text(f.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        raise FleetHostsUnreadable(f, getattr(exc, "strerror", None) or str(exc)) from exc


# --- the generated /etc/hosts fleet block -------------------------------------
#
# Ported from MeshForge 2026-09-07 (MF is the lead repo). ⚠️ These literals are
# deliberately NOT namespaced to meshanchor, unlike the rest of this module:
# the block is written by MeshForge's ``scripts/gen_fleet_hosts.py``, ONE per
# box, and both twins are READERS of that single artifact. Namespacing them
# would make this reader find no block at all — silently, since an absent block
# is indistinguishable from a box with no fleet.
HOSTS_BLOCK_BEGIN = "# BEGIN meshforge fleet (generated by scripts/gen_fleet_hosts.py)"
HOSTS_BLOCK_END = "# END meshforge fleet"
DEFAULT_DOMAIN = "mf.internal"


def parse_hosts_block(text: str) -> List[Tuple[str, List[str]]]:
    """``[(addr, [name, ...]), ...]`` from the DELIMITED meshforge block only.

    A second membership registry, and the only one present on EVERY box:
    ``fleet_hosts`` lives on the manager, while this block is generated and
    hourly self-healed on all of them.

    ⚠️ **Only ever the delimited block.** Parsing the whole file would make
    ``localhost``, the ``127.0.1.1`` line and any hand-added entry count as
    fleet members — widening what is "expected" is the one direction a
    membership registry must never fail in.

    ⚠️ **A torn block yields nothing.** BEGIN without END means the artifact
    is damaged; the safe read is to know less, not to guess more.
    """
    if HOSTS_BLOCK_BEGIN not in text or HOSTS_BLOCK_END not in text:
        return []
    body = text.split(HOSTS_BLOCK_BEGIN, 1)[1].split(HOSTS_BLOCK_END, 1)[0]
    out: List[Tuple[str, List[str]]] = []
    for raw in body.splitlines():
        fields = raw.split("#", 1)[0].split()
        if len(fields) < 2:
            continue
        addr = fields[0].strip().lower()
        names = [h.strip().lower().rstrip(".") for h in fields[1:] if h.strip()]
        if addr and names:
            out.append((addr, names))
    return out
